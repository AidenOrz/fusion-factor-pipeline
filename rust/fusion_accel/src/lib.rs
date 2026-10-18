//! Cross-sectional kernels that are slow in Python and trivial in Rust.
//!
//! These are pure functions over already-loaded data. They deliberately do NOT
//! read files or own any pipeline state: the Python side keeps every decision
//! about what to compute, and this crate only makes the inner loops fast. That
//! keeps the numerical contract testable from both sides.

mod python;

use std::collections::{HashMap, HashSet};

/// Bottom-fraction exclusion set of a single cross-section.
///
/// Ranking is ascending on the score (lowest score is worst), ties broken by
/// first appearance, matching the pandas rank(ascending=True, method="first")
/// behaviour the Python pipeline uses. Getting the tie rule wrong would
/// silently change membership, so it is stated here explicitly.
pub fn bottom_excluded(scores: &[f64], fraction: f64) -> Vec<bool> {
    let n = scores.len();
    if n == 0 || !(fraction > 0.0) {
        return vec![false; n];
    }
    let cut = ((fraction * n as f64).ceil() as usize).min(n);

    let mut order: Vec<usize> = (0..n).collect();
    // Stable sort keeps original index order among equal scores, which is what
    // "method=first" means.
    order.sort_by(|&a, &b| {
        scores[a]
            .partial_cmp(&scores[b])
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut out = vec![false; n];
    for &idx in order.iter().take(cut) {
        out[idx] = true;
    }
    out
}

/// Membership churn between two consecutive exclusion sets.
///
/// Returns the change rate defined exactly as the C-track does it:
///     1 - |prev & cur| / |prev|
/// i.e. the share of yesterday's excluded names that are no longer excluded.
/// The denominator is the PREVIOUS set, not the current one; mixing the two is
/// the easiest way to report a churn number that looks plausible and is wrong.
pub fn membership_change_rate(prev: &HashSet<String>, cur: &HashSet<String>) -> Option<f64> {
    if prev.is_empty() {
        return None;
    }
    let kept = prev.intersection(cur).count();
    Some(1.0 - kept as f64 / prev.len() as f64)
}

/// Daily Spearman rank correlation between two aligned score vectors.
///
/// Returns None when the cross-section is too small or degenerate, matching
/// the Python guard (needs at least min_distinct distinct values on each side
/// and at least min_names rows).
pub fn spearman_ic(
    scores: &[f64],
    target: &[f64],
    min_distinct: usize,
    min_names: usize,
) -> Option<f64> {
    let n = scores.len();
    if n != target.len() || n < min_names {
        return None;
    }
    let (rs, rt) = (average_ranks(scores), average_ranks(target));
    if distinct_count(&rs) < min_distinct || distinct_count(&rt) < min_distinct {
        return None;
    }
    let mean_s = rs.iter().sum::<f64>() / n as f64;
    let mean_t = rt.iter().sum::<f64>() / n as f64;
    let mut num = 0.0;
    let mut den_s = 0.0;
    let mut den_t = 0.0;
    for i in 0..n {
        let ds = rs[i] - mean_s;
        let dt = rt[i] - mean_t;
        num += ds * dt;
        den_s += ds * ds;
        den_t += dt * dt;
    }
    let den = (den_s * den_t).sqrt();
    if den <= 0.0 {
        return None;
    }
    Some(num / den)
}

/// Average ranks (1-based); tied values receive the mean of their positions.
pub fn average_ranks(values: &[f64]) -> Vec<f64> {
    let n = values.len();
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&a, &b| {
        values[a]
            .partial_cmp(&values[b])
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut ranks = vec![0.0; n];
    let mut i = 0;
    while i < n {
        let mut j = i + 1;
        while j < n && values[order[j]] == values[order[i]] {
            j += 1;
        }
        let avg = ((i + 1) as f64 + j as f64) / 2.0;
        for k in i..j {
            ranks[order[k]] = avg;
        }
        i = j;
    }
    ranks
}

fn distinct_count(values: &[f64]) -> usize {
    let mut seen: Vec<f64> = values.to_vec();
    seen.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    seen.dedup();
    seen.len()
}

/// Churn series over date-ordered exclusion sets.
pub fn churn_series(sets: &[HashSet<String>]) -> Vec<Option<f64>> {
    let mut out = Vec::with_capacity(sets.len().saturating_sub(1));
    for window in sets.windows(2) {
        out.push(membership_change_rate(&window[0], &window[1]));
    }
    out
}

/// Per-key mean, used for the excluded-group excess profile.
pub fn group_means(keys: &[String], values: &[f64]) -> HashMap<String, f64> {
    let mut sum: HashMap<String, (f64, usize)> = HashMap::new();
    for (key, value) in keys.iter().zip(values.iter()) {
        let entry = sum.entry(key.clone()).or_insert((0.0, 0));
        entry.0 += *value;
        entry.1 += 1;
    }
    sum.into_iter()
        .map(|(k, (s, n))| (k, s / n as f64))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bottom_excluded_takes_the_ceil_share() {
        let scores = vec![0.9, 0.1, 0.5, 0.3, 0.7, 0.2, 0.8, 0.4, 0.6, 0.0];
        let ex = bottom_excluded(&scores, 0.1);
        assert_eq!(ex.iter().filter(|v| **v).count(), 1);
        assert!(ex[9], "the single lowest score must be excluded");
    }

    #[test]
    fn bottom_excluded_is_stable_on_ties() {
        let scores = vec![0.5, 0.5, 0.5, 0.5];
        let ex = bottom_excluded(&scores, 0.5);
        assert_eq!(ex, vec![true, true, false, false]);
    }

    #[test]
    fn change_rate_uses_the_previous_set_as_denominator() {
        let prev: HashSet<String> = ["a", "b"].iter().map(|s| s.to_string()).collect();
        let cur: HashSet<String> = ["b", "c"].iter().map(|s| s.to_string()).collect();
        assert_eq!(membership_change_rate(&prev, &cur), Some(0.5));
    }

    #[test]
    fn change_rate_is_none_for_an_empty_previous_set() {
        let empty: HashSet<String> = HashSet::new();
        let cur: HashSet<String> = ["a"].iter().map(|s| s.to_string()).collect();
        assert_eq!(membership_change_rate(&empty, &cur), None);
    }

    #[test]
    fn spearman_matches_a_known_perfect_correlation() {
        let a = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0];
        let b = vec![10.0, 20.0, 30.0, 40.0, 50.0, 60.0];
        let ic = spearman_ic(&a, &b, 5, 5).unwrap();
        assert!((ic - 1.0).abs() < 1e-12);
    }

    #[test]
    fn spearman_handles_ties_with_average_ranks() {
        let ranks = average_ranks(&[10.0, 20.0, 20.0, 30.0]);
        assert_eq!(ranks, vec![1.0, 2.5, 2.5, 4.0]);
    }

    #[test]
    fn spearman_rejects_degenerate_input() {
        let a = vec![1.0; 10];
        let b: Vec<f64> = (0..10).map(|i| i as f64).collect();
        assert!(spearman_ic(&a, &b, 5, 5).is_none(), "constant side is degenerate");
    }
}
