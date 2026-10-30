# 待修队列（自动生成自台账 + D 节已修清单）

总条目=319 已修=301 判为非缺陷=8 待修=10

| 域 | critical | high | medium-high | medium | low | latent | untagged | 合计 |
|---|---|---|---|---|---|---|---|---|
| backend | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 6 |
| fe | 0 | 0 | 0 | 0 | 3 | 1 | 0 | 4 |
| plugin | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| infra | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| deploy | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## backend / latent (6)

- **A259** `(unresolved)` — 开发者菜单白名单命中即整节点放行（latent，当前靠一个命名约定在撑）
- **A282** `app/api/federation.py` — 远端空间标签靠 `name` 前缀 + `LIKE` 过滤，`%`/`_` 没转义（latent / low，我把自己推的危害调低了）
- **A125** `app/models/policy_bundle.py` — policy_bundle 模型层两条 high 随 A73 一起降级（潜伏）
- **A131** `app/services/k_anonymity.py` — k_anonymity 只校验首行的准标识符存在性，缺失值归入同一个等价类（潜伏，隐私门可假通过）
- **A128** `app/services/k8s_client.py` — k8s_client 的 RWX 默认 StorageClass 猜选与 exec 透传（潜伏）
- **A73** `contract_service.py` — 已撤销的策略包继续做授权决策（**已降级→潜伏**，见第二十九批“A73 定级修正”）

## fe / low (3)

- **A210** `DevTaskCreatePage.tsx` — 「配额列小字」靠 `nth-child(3)` 定位，背地里也打到了另一张卡的 `export_mode`（low，可维护性）
- **A225** `NotebookWorkbench.tsx` — 同文件卫生小项族（low，但 F-71 那一半是潜伏）
- **A234** `TaskDetail.tsx` — 本屏卫生族四条（low；其中 ocr 当现行缺陷的一条实测为潜伏）

## fe / latent (1)

- **A270** `routeCrumb.ts` — useCrumbSegments 两条降为 low（latent）

## 后端按文件聚合（给互斥修波用）

- `contract_service.py` (1) A73/latent
- `app/models/policy_bundle.py` (1) A125/latent
- `app/services/k8s_client.py` (1) A128/latent
- `app/services/k_anonymity.py` (1) A131/latent
- `(unresolved)` (1) A259/latent
- `app/api/federation.py` (1) A282/latent

文件总数(后端待修)=6