# Descriptive cost-model review, 2026-09-06

The final sensitivity equation preserves the direct baseline separately for
hit-category and non-hit-category requests. Let `h,m` be the corresponding
optimized means and `d_h,d_m` the direct means. Equating the two affine mixtures
gives `p* = (m-d_m) / ((m-d_m)+(d_h-h))` when that denominator is positive.

Earlier drafting used a single pooled direct mean. That would hold direct cost
fixed while changing the request-category mixture. The reviewed equation avoids
that unnecessary approximation. This is a secondary descriptive model, reviewed
after pilot observations; it is not a preregistered hypothesis, inferred agent
repeat rate, optimized threshold, or release gate. No observed timing, request,
primary end-to-end ratio, or dataset denominator changes. Conditional means and
the formula are supplied so readers can recompute the result.

Values outside zero to one identify a cost regime with no interior crossover;
they are not feasible negative or greater-than-total repeat rates. Within-category
costs are held fixed in this simple model, so correlated edit complexity and
runtime effects remain outside its predictions.
