# 门证据扫描（自动生成，只认日志里的汇总行）

日志数=169  绿（RC=0）=31  红（RC=1）=34  无结论（无汇总行）=45  红（有失败明细）=12  空文件（无结论）=13  绿（有通过汇总）=18  红（tsc）=6  红（RC=1） ⚠名字说绿、RC 非 0=6  红（RC=2）=1  红（RC=2） ⚠名字说绿、RC 非 0=1  红（RC=4） ⚠名字说绿、RC 非 0=1  红（RC=4）=1

| 时间 | 行数 | 判定 | 汇总行 | 文件 |
|---|---|---|---|---|
| 21:31 | 1 | 绿（RC=0） | TSC_RC=0 | .gate-fe-tsc-base.log |
| 21:31 | 36 | 红（RC=1） | VITEST_RC=1 | .gate-fe-vitest-base.log |
| 21:33 | 2 | 无结论（无汇总行） | 2 passed in 0.69s | .gate-b2.log |
| 21:36 | 24 | 无结论（无汇总行） | ========================================================================================== | .gate-b1.log |
| 21:37 | 3 | 无结论（无汇总行） | 83 passed in 68.23s (0:01:08) | .gate-base2-pytest.log |
| 21:37 | 451 | 红（RC=1） | VITEST_RC=1 | .gate-base2-vitest.log |
| 21:37 | 11 | 绿（RC=0） | PY_RC=0 | .gate-base2.log |
| 21:37 | 21 | 无结论（无汇总行） |    Duration  322ms (transform 0ms, setup 0ms, import 0ms, tests 0ms, environment 0ms) | .gate-f1-probe.log |
| 21:38 | 176 | 红（有失败明细） | Test Files  3 failed (3) | .gate-f1-before.log |
| 21:38 | 0 | 空文件（无结论） | - | .gate-f1-tsc.log |
| 21:38 | 19 | 绿（有通过汇总） | Test Files  3 passed (3) | .gate-f1.log |
| 21:40 | 148 | 无结论（无汇总行） | 9 failed, 67 passed in 106.30s (0:01:46) | .gate-b3-before.log |
| 21:41 | 10 | 红（tsc） | error TS2686: 'React' refers to a UMD global, but the current file is a module. Consider a | .gate-f3-tsc-baseline.log |
| 21:42 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f3-preexisting.log |
| 21:42 | 282 | 红（有失败明细） | Test Files  2 failed / 1 passed (3) | .gate-f3-red.log |
| 21:43 | 0 | 空文件（无结论） | - | .gate-f3-lint.log |
| 21:43 | 0 | 空文件（无结论） | - | .gate-f3-tsc-scoped.log |
| 21:43 | 19 | 绿（有通过汇总） | Test Files  3 passed (3) | .gate-f3.log |
| 21:44 | 3 | 无结论（无汇总行） | 76 passed in 107.66s (0:01:47) | .gate-b3.log |
| 21:44 | 2016 | 红（有失败明细） | Test Files  3 failed (3) | .gate-f2-red.log |
| 21:44 | 29 | 绿（有通过汇总） | Test Files  8 passed (8) | .gate-f3-neighbours.log |
| 21:44 | 2 | 红（tsc） | error TS2322: Type 'null' is not assignable to type 'AnchoringDisclosure / undefined'. | .gate-f3-tsc.log |
| 21:44 | 1205 | 红（有失败明细） | Test Files  1 failed (1) | .gate-f4-red-login.log |
| 21:45 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f2-f.log |
| 21:46 | 644 | 红（有失败明细） | Test Files  2 failed (2) | .gate-f4-red.log |
| 21:47 | 0 | 空文件（无结论） | - | .gate-f5-tsc.log |
| 21:47 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f5.log |
| 21:48 | 19 | 绿（有通过汇总） | Test Files  2 passed (2) | .gate-f4-rerun.log |
| 21:49 | 0 | 空文件（无结论） | - | .gate-f2-eslint.log |
| 21:49 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f4-routes.log |
| 21:50 | 147 | 无结论（无汇总行） | 5 failed, 62 passed in 309.87s (0:05:09) | .gate-b3-adjacent.log |
| 21:50 | 34 | 绿（有通过汇总） | Test Files  2 passed (2) | .gate-f2-existing.log |
| 21:50 | 4 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | .gate-f2-tsc2.log |
| 21:50 | 39 | 绿（有通过汇总） | Test Files  3 passed (3) | .gate-f2.log |
| 21:50 | 141 | 无结论（无汇总行） | [0m | .gate-f4-lint-all.log |
| 21:50 | 4 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | .gate-f4-tsc.log |
| 21:50 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f6-probe.log |
| 21:50 | 190 | 红（有失败明细） | Test Files  3 failed (3) | .gate-f6-red.log |
| 21:51 | 0 | 空文件（无结论） | - | .gate-f2-tsc-scoped.log |
| 21:52 | 2 | 红（tsc） | error TS2305: Module '"../routeCrumb"' has no exported member 'documentTitleFor'. | .gate-f2-tsc.log |
| 21:52 | 0 | 空文件（无结论） | - | .gate-f4-lint.log |
| 21:52 | 32 | 无结论（无汇总行） |    Duration  305ms (transform 0ms, setup 0ms, import 0ms, tests 0ms, environment 0ms) | .gate-f4-noflag.log |
| 21:52 | 0 | 空文件（无结论） | - | .gate-f4-tsc-scoped.log |
| 21:52 | 19 | 绿（有通过汇总） | Test Files  2 passed (2) | .gate-f4.log |
| 21:52 | 5 | 绿（RC=0） | PY_RC=0 | .gate-final-pytest.log |
| 21:54 | 149 | 红（RC=1） | BASE_RC=1 | .gate-base-p0.log |
| 21:56 | 0 | 空文件（无结论） | - | .gate-f6-tsc.log |
| 21:56 | 43 | 红（有失败明细） | Test Files  1 failed (1) | .gate-f7-red.log |
| 21:57 | 75 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-f6-corpus.log |
| 21:57 | 106 | 红（有失败明细） | Test Files  1 failed (1) | .gate-f6-red2.log |
| 21:57 | 238 | 绿（有通过汇总） | Test Files  34 passed (34) | .gate-f6-regression.log |
| 21:57 | 17 | 绿（有通过汇总） | Test Files  2 passed (2) | .gate-f7.log |
| 21:57 | 0 | 空文件（无结论） | - | .gate-final-tsc.log |
| 21:58 | 19 | 绿（有通过汇总） | Test Files  3 passed (3) | .gate-f6.log |
| 22:00 | 391 | 绿（有通过汇总） | Test Files  58 passed (58) | .gate-final-fe.log |
| 22:14 | 2 | 无结论（无汇总行） | 10 passed in 6.10s | .gate-baseline.log |
| 22:15 | 329 | 无结论（无汇总行） | 9 failed, 11 passed, 8 warnings in 10.11s | .gate-fix-red.log |
| 22:16 | 18 | 无结论（无汇总行） | 20 passed, 8 warnings in 9.70s | .gate-fix-green.log |
| 22:17 | 2111 | 无结论（无汇总行） | 4 failed, 7 passed in 5.39s | .gate-cubedist-red.log |
| 22:17 | 1108 | 无结论（无汇总行） | 10 failed, 1 passed in 5.92s | .gate-devdata-red.log |
| 22:18 | 2 | 无结论（无汇总行） | 2 passed in 0.71s | .gate-cors.log |
| 22:18 | 365 | 无结论（无汇总行） | 17 failed, 5 passed in 5.87s | .gate-watermark-red.log |
| 22:19 | 803 | 无结论（无汇总行） | 1 failed, 10 passed in 9.90s | .gate-devdata-green.log |
| 22:21 | 19 | 绿（RC=0） | RC=0 | .gate-a30-green.log |
| 22:21 | 2068 | 红（RC=1） ⚠名字说绿、RC 非 0 | C6GREEN_RC=1 | .gate-c6-green.log |
| 22:21 | 97 | 红（RC=1） | RC=1 | .gate-red-a48.log |
| 22:21 | 3765 | 无结论（无汇总行） | 12 failed, 170 passed, 16 skipped, 7 warnings in 256.70s (0:04:16) | .gate-regress.log |
| 22:22 | 3 | 绿（RC=0） | RC=0 | .gate-wm-green.log |
| 22:23 | 89 | 红（RC=1） | RC=1 | .gate-a129-red.log |
| 22:23 | 1978 | 红（RC=1） | RC=1 | .gate-a30-regress-fast.log |
| 22:23 | 4995 | 红（RC=1） | C6REGRESS_RC=1 | .gate-c6-regress.log |
| 22:24 | 3 | 绿（RC=0） | RC=0 | .gate-a129-green.log |
| 22:24 | 2068 | 红（RC=1） | CUBEDIST_RC=1 | .gate-c6b-cubedist.log |
| 22:26 | 21 | 绿（RC=0） | RC=0 | .gate-a30-final.log |
| 22:26 | 1769 | 绿（RC=0） | RC=0 | .gate-a30-green2.log |
| 22:27 | 106 | 红（RC=1） | RC=1 | .gate-a111-red.log |
| 22:28 | 10 | 绿（RC=0） | RC=0 | .gate-a111-green.log |
| 22:28 | 1873 | 红（RC=1） | RC=1 | .gate-a30-e2e.log |
| 22:28 | 971 | 红（RC=1） | CCUBE_RC=1 | .gate-cd-cube-alone.log |
| 22:28 | 19 | 绿（RC=0） | PARENTVERIFY_RC=0 | .gate-parent-verify-f58.log |
| 22:29 | 971 | 红（RC=1） | CCOMB_RC=1 | .gate-cd-combined.log |
| 22:30 | 49 | 红（RC=2） | RC=2 | .gate-a185-adjacent.log |
| 22:30 | 3 | 绿（RC=0） | CCUBE_RC=0 | .gate-ce-cube.log |
| 22:31 | 4 | 绿（RC=0） | RC=0 | .gate-a185-adjacent2.log |
| 22:31 | 3 | 绿（RC=0） | G1_DEVDATA_RC=0 | .gate-cg-devdata.log |
| 22:31 | 3 | 绿（RC=0） | RC=0 | .gate-green-a48.log |
| 22:31 | 25 | 红（RC=1） | LOCK_RC=1 | .gate-lock-a48.log |
| 22:32 | 3 | 绿（RC=0） | RC=0 | .gate-a185-green2.log |
| 22:32 | 598 | 红（RC=1） | R1_DEVRED_RC=1 | .gate-cf-r1-devdata-red.log |
| 22:32 | 2164 | 红（RC=1） | R3_CUBERED_RC=1 | .gate-cf-r3-cube-red.log |
| 22:32 | 2164 | 红（RC=1） | R4_NOREFRESH_RC=1 | .gate-cf-r4-cube-norefresh.log |
| 22:32 | 3 | 绿（RC=0） | G2_FINAL_RC=0 | .gate-cg-final.log |
| 22:32 | 9 | 红（RC=1） ⚠名字说绿、RC 非 0 | WIDE_RC=1 | .gate-green-a48-wide.log |
| 22:33 | 106 | 红（RC=1） | RC=1 | .gate-a185-final-gate.log |
| 22:34 | 3 | 绿（RC=0） | PARENTVERIFY_F5A_RC=0 | .gate-parent-verify-f5a.log |
| 22:35 | 3 | 绿（RC=0） | RC=0 | .gate-a129-green2.log |
| 22:36 | 459 | 无结论（无汇总行） | 22 failed, 2 passed in 7.82s | .gate-a114-red.log |
| 22:36 | 1 | 绿（RC=0） | COMPILE_RC=0 | .gate-ch-compile.log |
| 22:36 | 103 | 红（RC=1） | G3_RC=1 | .gate-ch-final-wide.log |
| 22:36 | 56 | 红（RC=1） | R5_NOCOMMIT_RC=1 | .gate-ch-r5-cube-nocommit.log |
| 22:36 | 49 | 红（RC=1） | R6_SVCRED_RC=1 | .gate-ch-r6-service-red.log |
| 22:36 | 30 | 红（RC=1） | RC=1 | .gate-others-fail.log |
| 22:36 | 96 | 无结论（无汇总行） | 7 failed, 1 passed, 2 skipped in 2.66s | .gate-tee_capability-red.log |
| 22:37 | 2 | 无结论（无汇总行） | 24 passed in 7.37s | .gate-a114-green.log |
| 22:37 | 2 | 无结论（无汇总行） | 9 passed in 2.37s | .gate-a114-regress.log |
| 22:37 | 3 | 绿（RC=0） | G4_RC=0 | .gate-ci-final-confirm.log |
| 22:37 | 103 | 红（RC=1） | R7_PZ04HEAD_RC=1 | .gate-ci-r7-pz04-head.log |
| 22:37 | 157 | 无结论（无汇总行） | 7 failed, 4 passed in 3.45s | .gate-ocr_a183_attestation_trust_root-red.log |
| 22:37 | 55 | 红（RC=1） | PARENT_RED_RC=1 | .gate-parent-a148-red.log |
| 22:37 | 52 | 红（RC=1） | PARENT_RED2_RC=1 | .gate-parent-a148-red2.log |
| 22:37 | 85 | 无结论（无汇总行） | 2 failed, 7 passed in 3.01s | .gate-session_snapshots-red.log |
| 22:38 | 8 | 无结论（无汇总行） | 14 passed, 1 warning in 5.75s | .gate-a114-others.log |
| 22:38 | 8 | 绿（RC=0） | RC=0 | .gate-a185-fixed-probe.log |
| 22:38 | 7 | 绿（RC=0） | RC=0 | .gate-a185-head-probe.log |
| 22:38 | 1 | 绿（RC=0） | PYC_RC=0 | .gate-cj-pyc.log |
| 22:38 | 2 | 无结论（无汇总行） | 11 passed in 2.99s | .gate-ocr_a183_attestation_trust_root-green.log |
| 22:38 | 2 | 无结论（无汇总行） | 9 passed in 2.44s | .gate-session_snapshots-green.log |
| 22:38 | 2 | 无结论（无汇总行） | 8 passed, 2 skipped in 2.03s | .gate-tee_capability-green.log |
| 22:39 | 107 | 红（RC=1） | RC=1 | .gate-mine-final.log |
| 22:39 | 502 | 红（RC=1） ⚠名字说绿、RC 非 0 | PARENT_GREEN_RC=1 | .gate-parent-a148-green.log |
| 22:39 | 3 | 绿（RC=0） | PARENT_F5D_RC=0 | .gate-parent-verify-f5d.log |
| 22:40 | 3 | 绿（RC=0） | RC=0 | .gate-ocr-b2.log |
| 22:40 | 103 | 红（RC=1） | RC=1 | .gate-pz04-alone.log |
| 22:40 | 2 | 无结论（无汇总行） | 23 passed in 5.13s | .gate-regression.log |
| 22:40 | 46 | 无结论（无汇总行） | 1 failed, 5 passed in 2.51s | .gate-regression2.log |
| 22:41 | 46 | 无结论（无汇总行） | 1 failed in 1.21s | .gate-asyncops-orig.log |
| 22:41 | 46 | 无结论（无汇总行） | 1 failed in 1.13s | .gate-asyncops1.log |
| 22:41 | 46 | 无结论（无汇总行） | 1 failed in 1.20s | .gate-asyncops2.log |
| 22:41 | 46 | 无结论（无汇总行） | 1 failed in 1.15s | .gate-asyncops3.log |
| 22:41 | 347 | 无结论（无汇总行） | 15 failed, 3 passed, 2 warnings in 5.21s | .gate-ops-red.log |
| 22:41 | 73 | 红（RC=1） | A045_RED_RC=1 | .gate-parent-a045-red.log |
| 22:42 | 19 | 红（RC=2） ⚠名字说绿、RC 非 0 | A045_GREEN_RC=2 | .gate-parent-a045-green.log |
| 22:43 | 685 | 无结论（无汇总行） | 28 failed, 7 passed in 32.22s | .gate-cubemode-red.log |
| 22:43 | 3 | 无结论（无汇总行） | 113 passed, 2 skipped in 69.28s (0:01:09) | .gate-final-green.log |
| 22:43 | 12 | 绿（RC=0） | RC=0 | .gate-ocr-b1.log |
| 22:44 | 208 | 红（RC=1） ⚠名字说绿、RC 非 0 | A045G2_RC=1 | .gate-parent-a045-green2.log |
| 22:44 | 3 | 绿（RC=0） | PARENT_F5B_RC=0 | .gate-parent-verify-f5b.log |
| 22:44 | 3 | 绿（RC=0） | PARENT_F5C_RC=0 | .gate-parent-verify-f5c.log |
| 22:45 | 190 | 红（RC=1） | BASECMP_RC=1 | .gate-base-a045-compare.log |
| 22:45 | 2 | 无结论（无汇总行） | 18 passed in 5.02s | .gate-ops-green.log |
| 22:45 | 3 | 绿（RC=0） | A045G3_RC=0 | .gate-parent-a045-green3.log |
| 22:49 | 30 | 红（RC=1） | PARENT_F5E_RC=1 | .gate-parent-verify-f5e.log |
| 22:50 | 48 | 红（RC=1） | A184_RED_RC=1 | .gate-parent-a184-red.log |
| 22:50 | 3 | 绿（RC=0） | ENVVAR_RC=0 | .gate-parent-envvar.log |
| 22:52 | 15 | 红（RC=1） | A027S_RC=1 | .gate-parent-a027-single.log |
| 22:52 | 379 | 红（RC=1） ⚠名字说绿、RC 非 0 | A184_GREEN_RC=1 | .gate-parent-a184-green.log |
| 22:52 | 8 | 红（RC=4） ⚠名字说绿、RC 非 0 | A184G2_RC=4 | .gate-parent-a184-green2.log |
| 22:53 | 360 | 红（RC=1） | BASEA184_RC=1 | .gate-base-a184-compare.log |
| 22:53 | 260 | 无结论（无汇总行） | 9 failed, 1 passed in 5.60s | .gate-data-resources-red.log |
| 22:53 | 4 | 绿（RC=0） | A184G3_RC=0 | .gate-parent-a184-green3.log |
| 22:54 | 2 | 无结论（无汇总行） | 12 passed in 5.76s | .gate-data-resources-green.log |
| 22:55 | 347 | 无结论（无汇总行） | 8 failed, 9 passed, 18 deselected in 41.39s | .gate-w5-a.log |
| 22:57 | 175 | 红（有失败明细） | Test Files  3 failed (3) | .gate-data-resources-fe-red.log |
| 22:58 | 3342 | 红（有失败明细） | Test Files  4 failed (4) | .gate-fe-a31x-red.log |
| 22:58 | 14 | 红（tsc） | error TS2305: Module '"../connectorApi"' has no exported member 'CONNECTOR_PAGE_LIMIT'. | .gate-fe-tsc.log |
| 22:58 | 131 | 无结论（无汇总行） | 4 failed, 9 passed, 22 deselected in 54.03s | .gate-w5-b.log |
| 22:59 | 0 | 空文件（无结论） | - | .gate-fe-tsc-mine.log |
| 23:01 | 0 | 空文件（无结论） | - | .gate-fe-lint.log |
| 23:02 | 39 | 红（RC=1） | A094_RED_RC=1 | .gate-parent-a094-red.log |
| 23:02 | 65 | 无结论（无汇总行） | 2 failed, 11 passed, 22 deselected in 61.88s (0:01:01) | .gate-w5-c.log |
| 23:03 | 8 | 红（RC=4） | BASEA094_RC=4 | .gate-base-a094-compare.log |
| 23:03 | 1227 | 无结论（无汇总行） | 13 failed, 135 passed in 149.00s (0:02:29) | .gate-data-resources-backend-set.log |
| 23:03 | 180 | 红（有失败明细） | Test Files  1 failed / 3 passed (4) | .gate-data-resources-fe-green.log |
| 23:03 | 15 | 绿（有通过汇总） | Test Files  1 passed (1) | .gate-fe-a31x-debug.log |
| 23:03 | 148 | 红（RC=1） ⚠名字说绿、RC 非 0 | A094_GREEN_RC=1 | .gate-parent-a094-green.log |
| 23:04 | 148 | 红（RC=1） | BASEA094B_RC=1 | .gate-base-a094-compare2.log |
| 23:04 | 44 | 无结论（无汇总行） | 2 failed, 4 passed, 29 deselected in 11.10s | .gate-w5-d.log |
| 23:05 | 3710 | 红（有失败明细） | Test Files  4 failed / 61 passed (65) | .gate-fe-full.log |
| 23:05 | 0 | 空文件（无结论） | - | .gate-scan-evidence.log |