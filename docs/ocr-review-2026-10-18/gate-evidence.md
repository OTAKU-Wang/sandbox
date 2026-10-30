# 门证据扫描（自动生成，只认日志里的汇总行）

日志数=208  绿（有通过汇总）=78  红（有失败明细）=83  红（RC=2）=1  绿（RC=0）=7  红（RC=4）=1  红（RC=1）=2  红（有失败明细） ⚠名字说绿、RC 非 0=6  无结论（无汇总行）=7  空文件（无结论）=11  红（tsc）=10  红（RC=2） ⚠名字说绿、RC 非 0=1  红（RC=4） ⚠名字说绿、RC 非 0=1

| 时间 | 行数 | 判定 | 汇总行 | 文件 |
|---|---|---|---|---|
| 2026-10-19 00:52 | 9 | 绿（有通过汇总） | 85 passed, 12 skipped, 1 warning in 20.44s | docs/ocr-review-2026-10-18/gate-logs/gate-a111-green.log |
| 2026-10-19 00:52 | 93 | 红（有失败明细） | 6 failed, 2 passed in 2.46s | docs/ocr-review-2026-10-18/gate-logs/gate-a111-red.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 24 passed in 7.37s | docs/ocr-review-2026-10-18/gate-logs/gate-a114-green.log |
| 2026-10-19 00:52 | 7 | 绿（有通过汇总） | 14 passed, 1 warning in 5.75s | docs/ocr-review-2026-10-18/gate-logs/gate-a114-others.log |
| 2026-10-19 00:52 | 393 | 红（有失败明细） | 22 failed, 2 passed in 7.82s | docs/ocr-review-2026-10-18/gate-logs/gate-a114-red.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 9 passed in 2.37s | docs/ocr-review-2026-10-18/gate-logs/gate-a114-regress.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 49 passed, 7 skipped in 16.67s | docs/ocr-review-2026-10-18/gate-logs/gate-a129-green.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 16 passed, 4 skipped in 4.41s | docs/ocr-review-2026-10-18/gate-logs/gate-a129-green2.log |
| 2026-10-19 00:52 | 72 | 红（有失败明细） | 6 failed, 10 passed, 4 skipped in 5.11s | docs/ocr-review-2026-10-18/gate-logs/gate-a129-red.log |
| 2026-10-19 00:52 | 47 | 红（RC=2） | RC=2 | docs/ocr-review-2026-10-18/gate-logs/gate-a185-adjacent.log |
| 2026-10-19 00:52 | 4 | 绿（有通过汇总） | 132 passed in 28.65s | docs/ocr-review-2026-10-18/gate-logs/gate-a185-adjacent2.log |
| 2026-10-19 00:52 | 92 | 红（有失败明细） | 4 failed, 276 passed, 8 skipped, 6 warnings in 251.95s (0:04:11) | docs/ocr-review-2026-10-18/gate-logs/gate-a185-final-gate.log |
| 2026-10-19 00:52 | 8 | 绿（RC=0） | RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-a185-fixed-probe.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 38 passed, 1 skipped in 13.33s | docs/ocr-review-2026-10-18/gate-logs/gate-a185-green2.log |
| 2026-10-19 00:52 | 7 | 绿（RC=0） | RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-a185-head-probe.log |
| 2026-10-19 00:52 | 1847 | 红（有失败明细） | 3 failed, 45 passed in 146.56s (0:02:26) | docs/ocr-review-2026-10-18/gate-logs/gate-a30-e2e.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | 21 passed, 8 warnings in 9.82s | docs/ocr-review-2026-10-18/gate-logs/gate-a30-final.log |
| 2026-10-19 00:52 | 17 | 绿（有通过汇总） | 22 passed, 8 warnings in 10.14s | docs/ocr-review-2026-10-18/gate-logs/gate-a30-green.log |
| 2026-10-19 00:52 | 1742 | 红（有失败明细） | 3 failed, 29 passed in 79.54s (0:01:19) | docs/ocr-review-2026-10-18/gate-logs/gate-a30-green2.log |
| 2026-10-19 00:52 | 1927 | 红（有失败明细） | 9 failed, 150 passed, 17 skipped, 7 warnings in 160.84s (0:02:40) | docs/ocr-review-2026-10-18/gate-logs/gate-a30-regress-fast.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 1 failed in 1.21s | docs/ocr-review-2026-10-18/gate-logs/gate-asyncops-orig.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 1 failed in 1.13s | docs/ocr-review-2026-10-18/gate-logs/gate-asyncops1.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 1 failed in 1.20s | docs/ocr-review-2026-10-18/gate-logs/gate-asyncops2.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 1 failed in 1.15s | docs/ocr-review-2026-10-18/gate-logs/gate-asyncops3.log |
| 2026-10-19 00:52 | 40 | 红（有失败明细） | Tests  2 failed (2) | docs/ocr-review-2026-10-18/gate-logs/gate-authapi-red.log |
| 2026-10-19 00:52 | 21 | 绿（有通过汇总） | 27 passed in 10.99s | docs/ocr-review-2026-10-18/gate-logs/gate-b1.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 2 passed in 0.69s | docs/ocr-review-2026-10-18/gate-logs/gate-b2.log |
| 2026-10-19 00:52 | 117 | 红（有失败明细） | 5 failed, 62 passed in 309.87s (0:05:09) | docs/ocr-review-2026-10-18/gate-logs/gate-b3-adjacent.log |
| 2026-10-19 00:52 | 127 | 红（有失败明细） | 9 failed, 67 passed in 106.30s (0:01:46) | docs/ocr-review-2026-10-18/gate-logs/gate-b3-before.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 76 passed in 107.66s (0:01:47) | docs/ocr-review-2026-10-18/gate-logs/gate-b3.log |
| 2026-10-19 00:52 | 155 | 红（有失败明细） | 6 failed, 25 passed in 56.35s | docs/ocr-review-2026-10-18/gate-logs/gate-base-a045-compare.log |
| 2026-10-19 00:52 | 6 | 红（RC=4） | BASEA094_RC=4 | docs/ocr-review-2026-10-18/gate-logs/gate-base-a094-compare.log |
| 2026-10-19 00:52 | 118 | 红（有失败明细） | 5 failed, 46 passed in 23.24s | docs/ocr-review-2026-10-18/gate-logs/gate-base-a094-compare2.log |
| 2026-10-19 00:52 | 325 | 红（有失败明细） | 9 failed, 92 passed in 38.57s | docs/ocr-review-2026-10-18/gate-logs/gate-base-a184-compare.log |
| 2026-10-19 00:52 | 495 | 红（有失败明细） | 12 failed, 36 passed in 17.71s | docs/ocr-review-2026-10-18/gate-logs/gate-base-dup.log |
| 2026-10-19 00:52 | 119 | 红（有失败明细） | 5 failed, 10 passed in 11.06s | docs/ocr-review-2026-10-18/gate-logs/gate-base-p0.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 83 passed in 68.23s (0:01:08) | docs/ocr-review-2026-10-18/gate-logs/gate-base2-pytest.log |
| 2026-10-19 00:52 | 451 | 红（RC=1） | VITEST_RC=1 | docs/ocr-review-2026-10-18/gate-logs/gate-base2-vitest.log |
| 2026-10-19 00:52 | 11 | 绿（RC=0） | PY_RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-base2.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 10 passed in 6.10s | docs/ocr-review-2026-10-18/gate-logs/gate-baseline.log |
| 2026-10-19 00:52 | 2046 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 2 failed, 20 passed in 14.28s | docs/ocr-review-2026-10-18/gate-logs/gate-c6-green.log |
| 2026-10-19 00:52 | 4935 | 红（有失败明细） | 6 failed, 44 passed in 99.76s (0:01:39) | docs/ocr-review-2026-10-18/gate-logs/gate-c6-regress.log |
| 2026-10-19 00:52 | 2046 | 红（有失败明细） | 2 failed, 9 passed in 5.47s | docs/ocr-review-2026-10-18/gate-logs/gate-c6b-cubedist.log |
| 2026-10-19 00:52 | 960 | 红（有失败明细） | 1 failed, 24 passed in 15.27s | docs/ocr-review-2026-10-18/gate-logs/gate-cd-combined.log |
| 2026-10-19 00:52 | 960 | 红（有失败明细） | 1 failed, 10 passed in 4.82s | docs/ocr-review-2026-10-18/gate-logs/gate-cd-cube-alone.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 11 passed in 4.24s | docs/ocr-review-2026-10-18/gate-logs/gate-ce-cube.log |
| 2026-10-19 00:52 | 567 | 红（有失败明细） | 10 failed, 1 passed in 6.90s | docs/ocr-review-2026-10-18/gate-logs/gate-cf-r1-devdata-red.log |
| 2026-10-19 00:52 | 2142 | 红（有失败明细） | 2 failed, 9 passed in 5.68s | docs/ocr-review-2026-10-18/gate-logs/gate-cf-r3-cube-red.log |
| 2026-10-19 00:52 | 2142 | 红（有失败明细） | 2 failed, 9 passed in 5.58s | docs/ocr-review-2026-10-18/gate-logs/gate-cf-r4-cube-norefresh.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 11 passed in 9.18s | docs/ocr-review-2026-10-18/gate-logs/gate-cg-devdata.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 25 passed in 12.86s | docs/ocr-review-2026-10-18/gate-logs/gate-cg-final.log |
| 2026-10-19 00:52 | 1 | 绿（RC=0） | COMPILE_RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-ch-compile.log |
| 2026-10-19 00:52 | 91 | 红（有失败明细） | 4 failed, 42 passed in 18.27s | docs/ocr-review-2026-10-18/gate-logs/gate-ch-final-wide.log |
| 2026-10-19 00:52 | 53 | 红（有失败明细） | 1 failed, 10 passed in 4.63s | docs/ocr-review-2026-10-18/gate-logs/gate-ch-r5-cube-nocommit.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 2 failed, 9 passed in 4.65s | docs/ocr-review-2026-10-18/gate-logs/gate-ch-r6-service-red.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 28 passed in 14.24s | docs/ocr-review-2026-10-18/gate-logs/gate-ci-final-confirm.log |
| 2026-10-19 00:52 | 91 | 红（有失败明细） | 4 failed, 14 passed in 5.14s | docs/ocr-review-2026-10-18/gate-logs/gate-ci-r7-pz04-head.log |
| 2026-10-19 00:52 | 1 | 绿（RC=0） | PYC_RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-cj-pyc.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 2 passed in 0.71s | docs/ocr-review-2026-10-18/gate-logs/gate-cors.log |
| 2026-10-19 00:52 | 29 | 无结论（无汇总行） |    Duration  56ms (transform 0ms, setup 0ms, import 0ms, tests 0ms, environment 0ms) | docs/ocr-review-2026-10-18/gate-logs/gate-crypto-red.log |
| 2026-10-19 00:52 | 2083 | 红（有失败明细） | 4 failed, 7 passed in 5.39s | docs/ocr-review-2026-10-18/gate-logs/gate-cubedist-red.log |
| 2026-10-19 00:52 | 599 | 红（有失败明细） | 28 failed, 7 passed in 32.22s | docs/ocr-review-2026-10-18/gate-logs/gate-cubemode-red.log |
| 2026-10-19 00:52 | 1217 | 红（有失败明细） | 13 failed, 135 passed in 149.00s (0:02:29) | docs/ocr-review-2026-10-18/gate-logs/gate-data-resources-backend-set.log |
| 2026-10-19 00:52 | 30 | 绿（有通过汇总） | Tests  13 passed (13) | docs/ocr-review-2026-10-18/gate-logs/gate-data-resources-fe-green.log |
| 2026-10-19 00:52 | 175 | 红（有失败明细） | Tests  6 failed / 4 passed (10) | docs/ocr-review-2026-10-18/gate-logs/gate-data-resources-fe-red.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 12 passed in 5.76s | docs/ocr-review-2026-10-18/gate-logs/gate-data-resources-green.log |
| 2026-10-19 00:52 | 234 | 红（有失败明细） | 9 failed, 1 passed in 5.60s | docs/ocr-review-2026-10-18/gate-logs/gate-data-resources-red.log |
| 2026-10-19 00:52 | 792 | 红（有失败明细） | 1 failed, 10 passed in 9.90s | docs/ocr-review-2026-10-18/gate-logs/gate-devdata-green.log |
| 2026-10-19 00:52 | 1071 | 红（有失败明细） | 10 failed, 1 passed in 5.92s | docs/ocr-review-2026-10-18/gate-logs/gate-devdata-red.log |
| 2026-10-19 00:52 | 176 | 红（有失败明细） | Tests  5 failed / 4 passed (9) | docs/ocr-review-2026-10-18/gate-logs/gate-f1-before.log |
| 2026-10-19 00:52 | 21 | 无结论（无汇总行） |    Duration  322ms (transform 0ms, setup 0ms, import 0ms, tests 0ms, environment 0ms) | docs/ocr-review-2026-10-18/gate-logs/gate-f1-probe.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f1-tsc.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | Tests  9 passed (9) | docs/ocr-review-2026-10-18/gate-logs/gate-f1.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f2-eslint.log |
| 2026-10-19 00:52 | 34 | 绿（有通过汇总） | Tests  26 passed (26) | docs/ocr-review-2026-10-18/gate-logs/gate-f2-existing.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  7 passed (7) | docs/ocr-review-2026-10-18/gate-logs/gate-f2-f.log |
| 2026-10-19 00:52 | 2016 | 红（有失败明细） | Tests  15 failed / 6 passed (21) | docs/ocr-review-2026-10-18/gate-logs/gate-f2-red.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f2-tsc-scoped.log |
| 2026-10-19 00:52 | 2 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | docs/ocr-review-2026-10-18/gate-logs/gate-f2-tsc.log |
| 2026-10-19 00:52 | 4 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | docs/ocr-review-2026-10-18/gate-logs/gate-f2-tsc2.log |
| 2026-10-19 00:52 | 39 | 绿（有通过汇总） | Tests  21 passed (21) | docs/ocr-review-2026-10-18/gate-logs/gate-f2.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f3-lint.log |
| 2026-10-19 00:52 | 29 | 绿（有通过汇总） | Tests  107 passed (107) | docs/ocr-review-2026-10-18/gate-logs/gate-f3-neighbours.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  4 passed (4) | docs/ocr-review-2026-10-18/gate-logs/gate-f3-preexisting.log |
| 2026-10-19 00:52 | 282 | 红（有失败明细） | Tests  17 failed / 33 passed (50) | docs/ocr-review-2026-10-18/gate-logs/gate-f3-red.log |
| 2026-10-19 00:52 | 10 | 红（tsc） | error TS2686: 'React' refers to a UMD global, but the current file is a module. Consider a | docs/ocr-review-2026-10-18/gate-logs/gate-f3-tsc-baseline.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f3-tsc-scoped.log |
| 2026-10-19 00:52 | 2 | 红（tsc） | error TS2322: Type 'null' is not assignable to type 'AnchoringDisclosure / undefined'. | docs/ocr-review-2026-10-18/gate-logs/gate-f3-tsc.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | Tests  50 passed (50) | docs/ocr-review-2026-10-18/gate-logs/gate-f3.log |
| 2026-10-19 00:52 | 139 | 无结论（无汇总行） | ✖ 26 problems (26 errors, 0 warnings) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-lint-all.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f4-lint.log |
| 2026-10-19 00:52 | 32 | 无结论（无汇总行） |    Duration  305ms (transform 0ms, setup 0ms, import 0ms, tests 0ms, environment 0ms) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-noflag.log |
| 2026-10-19 00:52 | 1205 | 红（有失败明细） | Tests  9 failed / 1 passed (10) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-red-login.log |
| 2026-10-19 00:52 | 644 | 红（有失败明细） | Tests  10 failed / 2 passed (12) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-red.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | Tests  12 passed (12) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-rerun.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  6 passed (6) | docs/ocr-review-2026-10-18/gate-logs/gate-f4-routes.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f4-tsc-scoped.log |
| 2026-10-19 00:52 | 4 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | docs/ocr-review-2026-10-18/gate-logs/gate-f4-tsc.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | Tests  12 passed (12) | docs/ocr-review-2026-10-18/gate-logs/gate-f4.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f5-tsc.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  4 passed (4) | docs/ocr-review-2026-10-18/gate-logs/gate-f5.log |
| 2026-10-19 00:52 | 75 | 绿（有通过汇总） | Tests  9 passed (9) | docs/ocr-review-2026-10-18/gate-logs/gate-f6-corpus.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  13 passed (13) | docs/ocr-review-2026-10-18/gate-logs/gate-f6-probe.log |
| 2026-10-19 00:52 | 190 | 红（有失败明细） | Tests  10 failed / 15 passed (25) | docs/ocr-review-2026-10-18/gate-logs/gate-f6-red.log |
| 2026-10-19 00:52 | 106 | 红（有失败明细） | Tests  5 failed / 4 passed (9) | docs/ocr-review-2026-10-18/gate-logs/gate-f6-red2.log |
| 2026-10-19 00:52 | 238 | 绿（有通过汇总） | Tests  391 passed (391) | docs/ocr-review-2026-10-18/gate-logs/gate-f6-regression.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-f6-tsc.log |
| 2026-10-19 00:52 | 19 | 绿（有通过汇总） | Tests  34 passed (34) | docs/ocr-review-2026-10-18/gate-logs/gate-f6.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | Tests  2 failed / 2 passed (4) | docs/ocr-review-2026-10-18/gate-logs/gate-f7-red.log |
| 2026-10-19 00:52 | 17 | 绿（有通过汇总） | Tests  14 passed (14) | docs/ocr-review-2026-10-18/gate-logs/gate-f7.log |
| 2026-10-19 00:52 | 15 | 绿（有通过汇总） | Tests  1 passed (1) | docs/ocr-review-2026-10-18/gate-logs/gate-fe-a31x-debug.log |
| 2026-10-19 00:52 | 3342 | 红（有失败明细） | Tests  20 failed / 2 passed (22) | docs/ocr-review-2026-10-18/gate-logs/gate-fe-a31x-red.log |
| 2026-10-19 00:52 | 3710 | 红（有失败明细） | Tests  20 failed / 619 passed (639) | docs/ocr-review-2026-10-18/gate-logs/gate-fe-full.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-fe-lint.log |
| 2026-10-19 00:52 | 1 | 绿（RC=0） | TSC_RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-fe-tsc-base.log |
| 2026-10-19 00:52 | 3 | 红（tsc） | error TS2591: Cannot find name 'node:fs'. Do you need to install type definitions for node | docs/ocr-review-2026-10-18/gate-logs/gate-fe-tsc-mine.log |
| 2026-10-19 00:52 | 5 | 红（tsc） | error TS2322: Type 'never[] / NoInfer<ConnectorListResult>' is not assignable to type 'rea | docs/ocr-review-2026-10-18/gate-logs/gate-fe-tsc.log |
| 2026-10-19 00:52 | 36 | 红（RC=1） | VITEST_RC=1 | docs/ocr-review-2026-10-18/gate-logs/gate-fe-vitest-base.log |
| 2026-10-19 00:52 | 391 | 绿（有通过汇总） | Tests  608 passed (608) | docs/ocr-review-2026-10-18/gate-logs/gate-final-fe.log |
| 2026-10-19 00:52 | 23 | 绿（有通过汇总） | 321 passed, 8 skipped, 6 warnings in 270.48s (0:04:30) | docs/ocr-review-2026-10-18/gate-logs/gate-final-gate.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 113 passed, 2 skipped in 69.28s (0:01:09) | docs/ocr-review-2026-10-18/gate-logs/gate-final-green.log |
| 2026-10-19 00:52 | 5 | 绿（有通过汇总） | 106 passed, 1 skipped in 118.60s (0:01:58) | docs/ocr-review-2026-10-18/gate-logs/gate-final-pytest.log |
| 2026-10-19 00:52 | 0 | 空文件（无结论） | - | docs/ocr-review-2026-10-18/gate-logs/gate-final-tsc.log |
| 2026-10-19 00:52 | 16 | 绿（有通过汇总） | 20 passed, 8 warnings in 9.70s | docs/ocr-review-2026-10-18/gate-logs/gate-fix-green.log |
| 2026-10-19 00:52 | 300 | 红（有失败明细） | 9 failed, 11 passed, 8 warnings in 10.11s | docs/ocr-review-2026-10-18/gate-logs/gate-fix-red.log |
| 2026-10-19 00:52 | 9 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 1 failed, 127 passed in 57.32s | docs/ocr-review-2026-10-18/gate-logs/gate-green-a48-wide.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 23 passed in 10.62s | docs/ocr-review-2026-10-18/gate-logs/gate-green-a48.log |
| 2026-10-19 00:52 | 25 | 红（有失败明细） | 4 failed, 17 passed in 7.23s | docs/ocr-review-2026-10-18/gate-logs/gate-lock-a48.log |
| 2026-10-19 00:52 | 95 | 红（有失败明细） | 4 failed, 318 passed, 20 skipped in 88.21s (0:01:28) | docs/ocr-review-2026-10-18/gate-logs/gate-mine-final.log |
| 2026-10-19 00:52 | 63 | 红（有失败明细） | Tests  3 failed / 21 passed (24) | docs/ocr-review-2026-10-18/gate-logs/gate-nbw-red.log |
| 2026-10-19 00:52 | 11 | 绿（有通过汇总） | 237 passed, 1 warning in 160.81s (0:02:40) | docs/ocr-review-2026-10-18/gate-logs/gate-ocr-b1.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 12 passed in 5.58s | docs/ocr-review-2026-10-18/gate-logs/gate-ocr-b2.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 11 passed in 2.99s | docs/ocr-review-2026-10-18/gate-logs/gate-ocr_a183_attestation_trust_root-green.log |
| 2026-10-19 00:52 | 136 | 红（有失败明细） | 7 failed, 4 passed in 3.45s | docs/ocr-review-2026-10-18/gate-logs/gate-ocr_a183_attestation_trust_root-red.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 18 passed in 5.02s | docs/ocr-review-2026-10-18/gate-logs/gate-ops-green.log |
| 2026-10-19 00:52 | 307 | 红（有失败明细） | 15 failed, 3 passed, 2 warnings in 5.21s | docs/ocr-review-2026-10-18/gate-logs/gate-ops-red.log |
| 2026-10-19 00:52 | 28 | 红（有失败明细） | 1 failed, 24 passed in 7.04s | docs/ocr-review-2026-10-18/gate-logs/gate-others-fail.log |
| 2026-10-19 00:52 | 15 | 红（有失败明细） | 1 failed in 1.10s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a027-single.log |
| 2026-10-19 00:52 | 18 | 红（RC=2） ⚠名字说绿、RC 非 0 | A045_GREEN_RC=2 | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a045-green.log |
| 2026-10-19 00:52 | 170 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 7 failed, 88 passed in 78.29s (0:01:18) | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a045-green2.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 10 passed in 4.36s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a045-green3.log |
| 2026-10-19 00:52 | 65 | 红（有失败明细） | 3 failed in 1.54s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a045-red.log |
| 2026-10-19 00:52 | 118 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 5 failed, 66 passed in 33.95s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a094-green.log |
| 2026-10-19 00:52 | 34 | 红（有失败明细） | 2 failed, 4 passed in 2.90s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a094-red.log |
| 2026-10-19 00:52 | 447 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 11 failed, 74 passed in 92.35s (0:01:32) | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a148-green.log |
| 2026-10-19 00:52 | 47 | 红（有失败明细） | 3 failed, 2 passed in 2.29s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a148-red.log |
| 2026-10-19 00:52 | 44 | 红（有失败明细） | 3 failed, 2 passed in 2.13s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a148-red2.log |
| 2026-10-19 00:52 | 341 | 红（有失败明细） ⚠名字说绿、RC 非 0 | 10 failed, 97 passed in 39.23s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a184-green.log |
| 2026-10-19 00:52 | 6 | 红（RC=4） ⚠名字说绿、RC 非 0 | A184G2_RC=4 | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a184-green2.log |
| 2026-10-19 00:52 | 4 | 绿（有通过汇总） | 75 passed in 30.78s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a184-green3.log |
| 2026-10-19 00:52 | 40 | 红（有失败明细） | 3 failed, 3 passed in 2.05s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-a184-red.log |
| 2026-10-19 00:52 | 495 | 红（有失败明细） | 12 failed, 36 passed in 18.41s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-dup-a148.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 22 passed in 4.75s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-envvar.log |
| 2026-10-19 00:52 | 454 | 红（有失败明细） | 36 failed, 11 passed in 10.79s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-straggler.log |
| 2026-10-19 00:52 | 310 | 红（有失败明细） | 12 failed, 1 passed in 3.64s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-straggler2.log |
| 2026-10-19 00:52 | 17 | 绿（有通过汇总） | 12 passed, 8 warnings in 7.56s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f58.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 11 passed in 3.99s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f5a.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 62 passed, 5 skipped in 18.74s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f5b.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 45 passed, 2 skipped in 10.48s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f5c.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 33 passed in 10.43s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f5d.log |
| 2026-10-19 00:52 | 28 | 红（有失败明细） | 1 failed, 39 passed in 10.21s | docs/ocr-review-2026-10-18/gate-logs/gate-parent-verify-f5e.log |
| 2026-10-19 00:52 | 91 | 红（有失败明细） | 4 failed, 14 passed in 5.03s | docs/ocr-review-2026-10-18/gate-logs/gate-pz04-alone.log |
| 2026-10-19 00:52 | 97 | 红（有失败明细） | 21 failed, 2 passed in 10.41s | docs/ocr-review-2026-10-18/gate-logs/gate-red-a48.log |
| 2026-10-19 00:52 | 3688 | 红（有失败明细） | 12 failed, 170 passed, 16 skipped, 7 warnings in 256.70s (0:04:16) | docs/ocr-review-2026-10-18/gate-logs/gate-regress.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 23 passed in 5.13s | docs/ocr-review-2026-10-18/gate-logs/gate-regression.log |
| 2026-10-19 00:52 | 43 | 红（有失败明细） | 1 failed, 5 passed in 2.51s | docs/ocr-review-2026-10-18/gate-logs/gate-regression2.log |
| 2026-10-19 00:52 | 3 | 绿（RC=0） | SCAN_RC=0 | docs/ocr-review-2026-10-18/gate-logs/gate-scan-evidence.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 9 passed in 2.44s | docs/ocr-review-2026-10-18/gate-logs/gate-session_snapshots-green.log |
| 2026-10-19 00:52 | 73 | 红（有失败明细） | 2 failed, 7 passed in 3.01s | docs/ocr-review-2026-10-18/gate-logs/gate-session_snapshots-red.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 8 passed, 2 skipped in 2.03s | docs/ocr-review-2026-10-18/gate-logs/gate-tee_capability-green.log |
| 2026-10-19 00:52 | 75 | 红（有失败明细） | 7 failed, 1 passed, 2 skipped in 2.66s | docs/ocr-review-2026-10-18/gate-logs/gate-tee_capability-red.log |
| 2026-10-19 00:52 | 23 | 绿（有通过汇总） | 321 passed, 8 skipped, 6 warnings in 267.37s (0:04:27) | docs/ocr-review-2026-10-18/gate-logs/gate-tonight-gate.log |
| 2026-10-19 00:52 | 324 | 红（有失败明细） | 8 failed, 9 passed, 18 deselected in 41.39s | docs/ocr-review-2026-10-18/gate-logs/gate-w5-a.log |
| 2026-10-19 00:52 | 120 | 红（有失败明细） | 4 failed, 9 passed, 22 deselected in 54.03s | docs/ocr-review-2026-10-18/gate-logs/gate-w5-b.log |
| 2026-10-19 00:52 | 59 | 红（有失败明细） | 2 failed, 11 passed, 22 deselected in 61.88s (0:01:01) | docs/ocr-review-2026-10-18/gate-logs/gate-w5-c.log |
| 2026-10-19 00:52 | 38 | 红（有失败明细） | 2 failed, 4 passed, 29 deselected in 11.10s | docs/ocr-review-2026-10-18/gate-logs/gate-w5-d.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 4 passed, 31 deselected in 1.57s | docs/ocr-review-2026-10-18/gate-logs/gate-w5-e.log |
| 2026-10-19 00:52 | 2 | 绿（有通过汇总） | 12 passed in 3.02s | docs/ocr-review-2026-10-18/gate-logs/gate-w5-f.log |
| 2026-10-19 00:52 | 321 | 红（有失败明细） | 17 failed, 5 passed in 5.87s | docs/ocr-review-2026-10-18/gate-logs/gate-watermark-red.log |
| 2026-10-19 00:52 | 3 | 绿（有通过汇总） | 56 passed, 1 skipped in 16.14s | docs/ocr-review-2026-10-18/gate-logs/gate-wm-green.log |
| 2026-10-19 01:53 | 118 | 绿（有通过汇总） | Tests  141 passed (141) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-16-fe-vitest-before.log |
| 2026-10-19 01:53 | 58 | 红（有失败明细） | 5 failed, 322 passed, 2 skipped, 7 warnings in 194.46s (0:03:14) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-40files-before.log |
| 2026-10-19 01:53 | 8 | 无结论（无汇总行） | STEP_OK schema_parity | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-backend-steps-before.log |
| 2026-10-19 01:53 | 2 | 绿（有通过汇总） | 20 passed in 6.15s | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-own4-before.log |
| 2026-10-19 01:53 | 2 | 绿（有通过汇总） | ss15   94 passed, 1 warning in 74.62s (0:01:14) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-ss15-before.log |
| 2026-10-19 01:53 | 400 | 红（有失败明细） | 73 failed, 154 passed, 1 warning in 69.53s (0:01:09) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-unv9-before.log |
| 2026-10-19 02:04 | 27 | 绿（有通过汇总） | 327 passed, 2 skipped, 7 warnings in 196.38s (0:03:16) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-40files-after.log |
| 2026-10-19 02:04 | 9 | 红（tsc） | error TS2591: Cannot find name 'node:fs/promises'. Do you need to install type definitions | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-fe-build-before.log |
| 2026-10-19 02:04 | 7 | 红（tsc） | error TS2591: Cannot find name 'node:fs/promises'. Do you need to install type definitions | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-fe-tsc-before.log |
| 2026-10-19 02:04 | 144 | 红（有失败明细） | Tests  5 failed / 644 passed (649) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-fe-vitest-before.log |
| 2026-10-19 02:04 | 3 | 绿（有通过汇总） | 94 passed in 68.39s (0:01:08) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-ss15-after.log |
| 2026-10-19 02:04 | 17 | 红（有失败明细） | 1 failed, 226 passed, 1 warning in 69.87s (0:01:09) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-unv9-after.log |
| 2026-10-19 02:21 | 11 | 红（tsc） | error TS2591: Cannot find name 'node:path'. Do you need to install type definitions for no | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-fe-lane5-rootcause.log |
| 2026-10-19 02:21 | 10 | 绿（有通过汇总） | 227 passed, 1 warning in 69.90s (0:01:09) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-unv9-final.log |
| 2026-10-19 03:45 | 89 | 无结论（无汇总行） | ======================= 39 passed, 12 warnings in 4.57s ======================== | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-a108-pz04-after.log |
| 2026-10-19 03:45 | 413 | 红（有失败明细） | 5 failed, 34 passed, 13 warnings, 4 errors in 10.54s | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-a108-pz04-before.log |
| 2026-10-19 03:45 | 25 | 绿（有通过汇总） | Tests  649 passed (649) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-local-fe-after.log |
| 2026-10-19 03:45 | 26 | 绿（有通过汇总） | 327 passed, 2 skipped, 7 warnings in 136.06s (0:02:16) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-local-gate40-after.log |
| 2026-10-19 03:45 | 389 | 红（有失败明细） | 4 failed, 14 passed, 3 warnings, 4 errors in 4.94s | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-pz04-alone-before.log |
| 2026-10-19 04:33 | 41 | 绿（有通过汇总） | 124 passed, 12 warnings in 42.61s | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-fixed-cluster-after.log |
| 2026-10-19 04:33 | 286 | 红（有失败明细） | 22 failed, 3710 passed, 5 skipped, 35 warnings in 946.10s (0:15:46) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-local-full-final.log |
| 2026-10-19 04:33 | 17 | 绿（有通过汇总） | 327 passed, 2 skipped, 3 warnings in 113.74s (0:01:53) | docs/ocr-review-2026-10-18/gate-logs/gate-2026-10-19-local-gate40-final.log |
| 2026-10-19 06:09 | 259 | 绿（有通过汇总） | Tests  649 passed (649) | docs/ocr-review-2026-10-18/gate-logs/fe-vitest.log |
| 2026-10-19 06:09 | 18 | 绿（有通过汇总） | 327 passed, 2 skipped, 3 warnings in 171.74s (0:02:51) | docs/ocr-review-2026-10-18/gate-logs/gate-r2.log |
| 2026-10-19 06:40 | 17 | 绿（有通过汇总） | 327 passed, 2 skipped, 3 warnings in 114.00s (0:01:53) | docs/ocr-review-2026-10-18/gate-logs/gate-final-40files.log |
| 2026-10-19 06:40 | 46 | 无结论（无汇总行） | [归档说明] 上方为被中断的部分运行（停在 83%，无终值，我早先一次 pkill 误杀）。终值见同目录 gate-final-40files.log：GATE_RC=0 / 32 | docs/ocr-review-2026-10-18/gate-logs/gate-final.log |
| 2026-10-19 06:55 | 141 | 红（有失败明细） | 2 failed, 3733 passed, 4 skipped, 39 warnings in 988.00s (0:16:28) | docs/ocr-review-2026-10-18/gate-logs/full-local-final-2026-10-19.log |
| 2026-10-19 06:55 | 17 | 绿（有通过汇总） | 327 passed, 2 skipped, 3 warnings in 114.00s (0:01:53) | docs/ocr-review-2026-10-18/gate-logs/gate-final-40files-r2.log |