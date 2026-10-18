//! Python bindings for the kernels, gated behind the `python` feature.
//!
//! The bindings are a thin shell: every kernel is defined and tested in the
//! pure-Rust module, so a bug cannot hide in the FFI layer, and the Python
//! side can fall back to a pure-Python implementation when the extension is
//! not built.

#[cfg(feature = "python")]
mod py {
    use numpy::PyReadonlyArray1;
    use pyo3::prelude::*;
    use std::collections::HashSet;

    /// Bottom-fraction exclusion flags for one cross-section.
    #[pyfunction]
    fn bottom_excluded_py(scores: PyReadonlyArray1<f64>, fraction: f64) -> Vec<bool> {
        let values = scores.as_slice().expect("contiguous array required");
        crate::bottom_excluded(values, fraction)
    }

    /// Spearman IC for one cross-section, or None when degenerate.
    #[pyfunction]
    #[pyo3(signature = (scores, target, min_distinct=5, min_names=5))]
    fn spearman_ic_py(
        scores: PyReadonlyArray1<f64>,
        target: PyReadonlyArray1<f64>,
        min_distinct: usize,
        min_names: usize,
    ) -> Option<f64> {
        let s = scores.as_slice().expect("contiguous array required");
        let t = target.as_slice().expect("contiguous array required");
        crate::spearman_ic(s, t, min_distinct, min_names)
    }

    /// Change rate between two exclusion sets (denominator = previous set).
    #[pyfunction]
    fn membership_change_rate_py(prev: Vec<String>, cur: Vec<String>) -> Option<f64> {
        let p: HashSet<String> = prev.into_iter().collect();
        let c: HashSet<String> = cur.into_iter().collect();
        crate::membership_change_rate(&p, &c)
    }

    /// Vectorised change-rate series over date-ordered membership lists.
    #[pyfunction]
    fn churn_series_py(sets: Vec<Vec<String>>) -> Vec<Option<f64>> {
        let owned: Vec<HashSet<String>> = sets
            .into_iter()
            .map(|s| s.into_iter().collect())
            .collect();
        crate::churn_series(&owned)
    }

    /// Average ranks of a vector.
    #[pyfunction]
    fn average_ranks_py(values: PyReadonlyArray1<f64>) -> Vec<f64> {
        let v = values.as_slice().expect("contiguous array required");
        crate::average_ranks(v)
    }

    // The Rust symbol cannot share its name with the crate, so the module
    // initialiser is named differently and renamed at the Python level.
    #[pymodule]
    #[pyo3(name = "fusion_accel")]
    fn fusion_accel_module(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(bottom_excluded_py, m)?)?;
        m.add_function(wrap_pyfunction!(spearman_ic_py, m)?)?;
        m.add_function(wrap_pyfunction!(membership_change_rate_py, m)?)?;
        m.add_function(wrap_pyfunction!(churn_series_py, m)?)?;
        m.add_function(wrap_pyfunction!(average_ranks_py, m)?)?;
        Ok(())
    }
}
