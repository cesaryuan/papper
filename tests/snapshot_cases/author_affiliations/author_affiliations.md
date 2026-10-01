---
title: Keyed author affiliations
authors:
  - name: First Researcher
    affiliations: [a, b]
  - name: Second Researcher
    affiliation: a
  - name: Corresponding Researcher
    affiliations: [b, a]
    email: unused@example.test
    corresponding: "Please contact Corresponding Researcher at custom@example.test."
affiliations:
  a: Shared University Laboratory
  b: Collaborative Research Institute
---

# Shared affiliations

Authors reuse institution keys, preserve their affiliation order, and supply
a complete custom correspondence sentence instead of the generated default.
