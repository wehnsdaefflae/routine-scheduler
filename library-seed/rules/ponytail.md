---
effect:
  with: checks existing code, standard libraries and native features before adding a custom implementation
  without: may build a new implementation before checking simpler available solutions
  when: writing or reviewing code and choosing dependencies
tags: [code, simplicity, efficiency]
---
# rule: Ponytail — minimal implementation

Understand the requested behaviour and trace the affected code before choosing a solution.
Then stop at the first adequate option:

1. Reuse an existing implementation in this codebase.
2. Use the standard library.
3. Use a native platform feature.
4. Use a dependency already installed.
5. Write the smallest clear implementation that meets the actual requirements.

Question speculative work; do not omit anything explicitly requested. Avoid abstractions for
imaginary callers and dependencies for features the platform already provides. Prefer explicit,
readable code over dense one-liners. Fix a shared root cause after checking its callers.

Keep boundary validation, data-loss handling, security, accessibility and required verification.
Follow the project's existing testing conventions. Preserve the scheduler's action format,
deliberation level and requested report detail. This rule changes implementation choices,
not reporting requirements, permissions or workflow control.

Adapted from DietrichGebert/ponytail, revision
`356918eba965ee1eac64bd3a7f0dd02108350de5` (MIT, copyright 2026 DietrichGebert).
Source: https://github.com/DietrichGebert/ponytail/tree/356918eba965ee1eac64bd3a7f0dd02108350de5
Scheduler adaptation: one optional mode; omits upstream hooks, output-style overrides and
one-line pressure. License: https://github.com/DietrichGebert/ponytail/blob/356918eba965ee1eac64bd3a7f0dd02108350de5/LICENSE

<!--
MIT License

Copyright (c) 2026 DietrichGebert

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
-->
