# 密态沙箱系统 · 技术规格说明书（Technical Spec）

> 版本：v1.0  
> 对齐标准：TC609-6-2025-01、TC609使用控制技术要求、NDI-TR-2025-02、GM/T国密系列  
> 受众：系统架构师、安全工程师、后端工程师、运维工程师

---

## 1. 系统架构总览

### 1.1 整体架构分层

```
┌─────────────────────────────────────────────────────────────────────┐
│                         接入层 Access Layer                          │
│  数商 SDK（Python/Java）  买方 SDK  管理控制台 Portal  跨空间连接器   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS/TLCP（国密 TLS）
┌──────────────────────────────▼──────────────────────────────────────┐
│                         网关层 Gateway Layer                         │
│  API Gateway（限流/鉴权/路由）  身份验证服务（SM2 证书）  合约验证中间件│
└────────┬─────────────────────┬──────────────────────────────────────┘
         │                     │
┌────────▼──────┐  ┌──────────▼───────────────────────────────────────┐
│  控制平面     │  │              数据平面 Data Plane                   │
│ Control Plane │  │                                                   │
│               │  │  ┌────────────────────────────────────────────┐   │
│ 合约管理服务  │  │  │          沙箱运行时 Sandbox Runtime           │   │
│ 密钥管理服务  │  │  │                                             │   │
│ 策略编译器    │  │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐   │   │
│ 任务调度器    │  │  │  │  L1 TEE │  │  L2 软件 │  │  L3 容器 │  │   │
│ 节点管理      │  │  │  │  沙箱   │  │  增强箱  │  │  沙箱   │  │   │
│               │  │  │  └────┬────┘  └────┬────┘  └────┬────┘  │   │
│               │  │  │       └────────────┴────────────┘       │   │
│               │  │  │              输出审查网关                  │   │
│               │  │  └────────────────────────────────────────────┘   │
└───────────────┘  └──────────────────────────────────────────────────┘
         │                     │
┌────────▼─────────────────────▼──────────────────────────────────────┐
│                      存储与密钥层 Storage & Key Layer                 │
│   加密对象存储（MinIO/Ceph）   KMS（HSM）   元数据库（PostgreSQL）     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                      审计与存证层 Audit Layer                         │
│    结构化日志（ClickHouse）   链上存证（FISCO BCOS）   监控（Prometheus）│
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 核心组件清单

| 组件 | 技术选型 | 职责 |
|------|----------|------|
| API 网关 | Kong / APISIX（国产化优先） | 限流、鉴权、路由、日志 |
| 身份服务 | 自研 SM2 PKI + LDAP | SM2 证书颁发、验证、吊销 |
| 合约服务 | 自研 + FISCO BCOS 合约层 | 数字合约全生命周期管理 |
| 密钥服务（KMS） | 开源 Vault（国密插件）+ HSM | SM4 密钥生成、分发、吊销 |
| 策略编译器 | 自研（XACML/REGO 方言） | 合约条款 → 可执行策略包 |
| 任务调度器 | 自研（支持 L1/L2/L3 路由） | 任务队列、节点选择、熔断 |
| L1 沙箱运行时 | Occlum（SGX）/ HyperEnclave（国产） | TEE 硬件隔离执行环境 |
| L2 沙箱运行时 | Firecracker microVM + gVisor | 软件增强隔离执行环境 |
| L3 沙箱运行时 | Docker + Seccomp-BPF + AppArmor | 最小化容器隔离 |
| 输出审查网关 | 自研规则引擎 + DLP 模型 | 结果合规过滤 + 脱敏 |
| 加密对象存储 | MinIO（SM4 服务端加密） | 密态数据产品存储 |
| 元数据库 | PostgreSQL 15 | 产品目录、会话状态、策略 |
| 审计日志 | ClickHouse（列存，高压缩） | 细粒度操作日志存储 |
| 链上存证 | FISCO BCOS（国产联盟链） | 日志摘要不可篡改存证 |
| 监控 | Prometheus + Grafana + Loki | 指标、告警、日志可观测 |
| 消息队列 | Kafka / Pulsar | 任务事件、审计流 |

---

## 2. 密码学体系设计

### 2.1 国密算法全景

```
┌──────────────────────────────────────────────────────────┐
│                    国密算法使用矩阵                         │
│                                                          │
│  用途                     算法         标准               │
│  ─────────────────────────────────────────────────────  │
│  数字签名（身份认证）      SM2-256      GB/T 32918        │
│  密钥交换（会话协商）      SM2-ECDH     GB/T 32918        │
│  消息摘要（完整性）        SM3-256      GB/T 32905        │
│  数据加密（存储）          SM4-GCM      GB/T 32907        │
│  数据加密（传输）          SM4-CBC      GB/T 32907        │
│  传输层协议               TLCP（GM/T 0024）               │
│  密钥标识（身份绑定）      SM9          GB/T 38635        │
│                                                          │
│  国际算法（兜底/互联互通）                                  │
│  ─────────────────────────────────────────────────────  │
│  跨境互联互通             AES-256-GCM + RSA-2048         │
│  开源 TEE 远程证明        ECDSA-P256（Intel 证书链）       │
└──────────────────────────────────────────────────────────┘
```

> **⚠️ gmssl 库限制**：Python `gmssl` 库不支持 SM4-GCM 模式（仅支持 SM4-CBC/ECB/CTR）。实现方案：
> - **推荐**：使用 `cryptography` 库的 AES-GCM + SM4 密钥派生（通过 HKDF）
> - **备选**：在 gmssl 基础上自行实现 GCM 模式（GHASH + GCTR）
> - **过渡方案**：SM4-CBC + HMAC-SM3 派生认证 tag（当前实现，安全性略低于原生 GCM）
>
> 所有规格文档中引用 `sm4_gcm_encrypt()` / `sm4_gcm_decrypt()` 的伪代码，实际实现使用上述方案之一。

### 2.2 密钥层次结构

```
Root CA 私钥（离线，HSM 保护）
  │
  ├── 中间 CA（KMS 服务证书）
  │     └── 节点证书（各沙箱节点 SM2 证书）
  │
  ├── 数商身份证书（SM2，颁发给数商机构）
  │     └── 数据产品密钥（Data Encryption Key，DEK）
  │           • SM4-256 随机生成，per-product
  │           • 由数商公钥加密后托管至 KMS
  │           • 合约激活时，KMS 用会话密钥重加密后下发至 TEE
  │
  ├── 买方身份证书（SM2，颁发给买方机构）
  │     └── 会话密钥（Session Key）
  │           • SM4 随机生成，per-session，短生命周期（≤24h）
  │           • 用于保护数商 DEK 的跨节点传输
  │
  └── 沙箱节点证书（SM2 + TEE 证书链）
        └── 内存加密密钥（MEK）
              • L1：由 CPU 硬件派生，不可导出
              • L2：由 KMS 分发的软件密钥，存于 tmpfs
```

### 2.3 数据加密方案

#### 2.3.1 结构化数据

```
原始数据（CSV/Parquet）
    │
    ├─ 列级加密：高敏感列（如身份证）使用 AES-SIV（确定性加密，支持等值查询）
    │            普通列使用 SM4-GCM（随机 IV，防重放）
    │
    └─ 行级加密：整行序列化后 SM4-GCM 加密，适用于行级权限控制
```

#### 2.3.2 非结构化数据

```
原始文件（DICOM/MP4/PDF/WAV）
    │
    ① 文件级 SM4-GCM 加密（整文件）
       → 密文存入对象存储，DEK 托管至 KMS
       → 文件 SM3 哈希写入元数据库（完整性校验）
    │
    ② 块级加密（大文件优化，>100MB）
       → 按固定块大小（16MB）分块
       → 每块独立 SM4-GCM 加密（不同 IV）
       → 块索引树结构（Merkle Tree）记录完整性
    │
    ③ 内容特征提取（入仓时预处理，结果在 TEE 内）
       → 图像：感知哈希（pHash）用于去重检索
       → 文档：标题/摘要向量（国产嵌入模型，如 BGE-M3）
       → 音视频：帧级哈希序列
       → 特征向量本身加密存储（SM4），不暴露原始内容
```

---

## 3. 沙箱运行时详细设计

### 3.1 L1 TEE 沙箱（标准级）

#### 3.1.1 技术选型与国产化路径

| 场景 | TEE 实现 | 配套框架 |
|------|----------|----------|
| Intel 服务器（主流云） | Intel SGX（DCAP 远程证明） | Occlum LibOS（蚂蚁开源，支持 Linux 兼容） |
| AMD EPYC 服务器 | AMD SEV-SNP | SVSM（Secure VM Service Module） |
| 鲲鹏服务器 | 华为 TrustZone（BoostKit TEE）| iTrustee SDK |
| 海光服务器 | 海光 CSV（Confidential and Secure Virtualization） | 海光官方 SDK |
| 龙芯/申威 | 软件 TEE 仿真（过渡期）| 参见 L2 方案 |

#### 3.1.2 Occlum 沙箱执行流程（SGX 代表性实现）

```
1. 任务预热（Enclave 初始化）
   ├─ 加载 Occlum LibOS 镜像至 SGX EPC 内存
   ├─ 初始化 POSIX 兼容文件系统（ tmpfs-in-enclave）
   └─ 生成 Quote（证明报告），提交至 DCAP 验证服务

2. 密钥接收（安全信道）
   ├─ KMS 验证 Quote 有效性（含 MRENCLAVE 度量值比对）
   ├─ KMS 生成临时会话密钥（SM4），用买方公钥加密传输
   └─ Enclave 内解密得到数据 DEK

3. 数据解密与加载
   ├─ 从加密对象存储取回密文数据块
   ├─ Enclave 内用 DEK 解密至受保护内存
   └─ 数据完整性校验（SM3 哈希比对）

4. 买方代码执行
   ├─ 代码静态扫描（沙箱外预扫描：AST 白名单检查）
   ├─ Enclave 内执行（Python 解释器 / SQL 引擎内嵌于 LibOS）
   ├─ 系统调用过滤（Occlum 内核过滤层）
   └─ 实时策略引擎校验每次数据访问

5. 结果输出
   ├─ 计算结果传至 Enclave 外
   ├─ 输出审查网关检查（见 §5）
   └─ 合规结果加密传输至买方

6. Enclave 销毁
   ├─ EPC 内存安全擦除（物理覆写）
   └─ DEK 销毁，KMS 记录密钥使用完成
```

#### 3.1.3 远程证明流程（DCAP）

```
沙箱节点                    Intel PCCS / 国产证明服务               KMS
    │                              │                               │
    ├─生成 Quote ────────────────>│                               │
    │  （含 MRENCLAVE+MRSIGNER）   │                               │
    │<─验证 Quote+返回证明证书─────┤                               │
    │                              │                               │
    ├─提交 Quote + 证明证书────────────────────────────────────>│
    │                                                              │
    │<─验证通过 → 下发数据 DEK（SM4 加密）──────────────────────────┤
```

**国产化替代方案**：使用国内 TEE 厂商提供的证明服务（如华为 iTrustee 证明服务、海光 CSV 证明服务），证明报告格式遵循 IETF RATS（RFC 9334）标准扩展国密版本。

### 3.2 L2 软件增强沙箱（降级级）

#### 3.2.1 双层隔离架构

```
┌─────────────────────────────────────────────────────────┐
│                Firecracker microVM（KVM）                │
│   独立 Linux 内核（精简版，~5MB），独立内存空间           │
│  ┌──────────────────────────────────────────────────┐  │
│  │               gVisor（runsc）                     │  │
│  │   用户态内核（Go 实现），拦截所有 syscall           │  │
│  │  ┌────────────────────────────────────────────┐  │  │
│  │  │           买方计算进程                       │  │  │
│  │  │  + Seccomp-BPF（syscall 白名单过滤，约40个）  │  │  │
│  │  │  + AppArmor（文件路径访问控制）               │  │  │
│  │  │  + eBPF 监控探针（实时行为采集）              │  │  │
│  │  └────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                │                │
    KVM 虚拟化        Network NS         OverlayFS
   硬件加速隔离       完全网络隔离        只读基础层
```

#### 3.2.2 MPC 补偿协议

L2 环境无硬件信任根，使用两方 MPC 确保计算节点无法单独获取原始数据：

```
数商侧（持有密钥分片 K1）        计算节点（持有密钥分片 K2）
         │                                │
         └──────── Secret Sharing ─────────┘
                         │
              Shamir (2,2) 门限方案：
              • K1 + K2 联合重组得到 DEK（仅在虚拟通道内）
              • 计算节点永远不单独持有完整 DEK
              • 数商侧监控 K2 的使用（有限次数合约绑定）
```

**MPC 协议选型**：SPDZ 协议变体（支持有限域上的乘法三元组预计算），优化为两方场景降低通信轮次至 O(1)。

#### 3.2.3 软件可信证明链

```
部署时（静态度量）：
① 基础镜像 SM3 哈希 → 写入 KMS 白名单
② gVisor runsc 二进制 SM3 哈希 → 写入 KMS 白名单
③ 策略包 SM3 哈希 → 绑定合约 ID

运行时（动态度量）：
④ 任务启动时：SM3(代码文件) 比对部署记录
⑤ eBPF 实时采集 syscall 序列 → 生成执行摘要
⑥ 任务结束时：SM3(执行摘要 + 输出结果) 写入审计链
```

#### 3.2.4 L2 专用 Seccomp 白名单

仅开放计算任务必要的系统调用（约 42 个，基于 docker 默认 seccomp 进一步收紧）：

```json
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {"names": ["read", "write", "open", "close", "fstat", "lseek"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["mmap", "mprotect", "munmap", "brk"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["rt_sigaction", "rt_sigreturn", "exit", "exit_group"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["futex", "nanosleep", "clock_gettime"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["getpid", "getuid", "getgid"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["epoll_create1", "epoll_wait", "epoll_ctl"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["recvfrom", "sendto"], "action": "SCMP_ACT_ALLOW",
     "comment": "仅允许与审计服务的环回通信"},
    // 显式拒绝高风险调用
    {"names": ["ptrace", "process_vm_readv", "process_vm_writev",
               "perf_event_open", "keyctl", "add_key"], "action": "SCMP_ACT_KILL"}
  ]
}
```

### 3.3 L3 最小化沙箱

用于低敏感数据或联调测试，不允许用于正式数据流通：

```yaml
# L3 容器配置
securityContext:
  runAsNonRoot: true
  runAsUser: 65534              # nobody 用户
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault           # 使用 Kubernetes 默认 seccomp 配置
```

数据处理方式：**全程同态加密（PHE 部分同态，支持加法和有限次乘法）**，计算节点仅处理密文，永远不接触明文。适用于聚合统计类（求和/均值/计数）任务。

---

## 4. 非结构化数据处理流水线

### 4.1 统一文件处理管道架构

```
                    非结构化数据入仓流水线
                    ─────────────────────────────────────────────
入口: 数商 SDK 上传
    │
    ▼
[格式检测 & 验证]
    • 检测 MIME 类型（基于魔数，不信任文件名后缀）
    • 恶意内容扫描（沙箱外：ClamAV + 自研规则）
    • 文件大小限制（单文件 ≤10GB）
    │
    ▼
[预处理 Router]  ──── 根据 MIME 类型分发 ────
    │
    ├─ image/* ──────────────────────────────────→ [图像处理流水线]
    │                                               TEE 内: 人脸检测 → 打码 → pHash 提取
    │
    ├─ application/pdf / application/msword ─────→ [文档处理流水线]
    │                                               TEE 内: PDF 解析 → OCR → NER 脱敏
    │
    ├─ audio/* ──────────────────────────────────→ [音频处理流水线]
    │                                               TEE 内: ASR → 说话人分离 → 敏感词屏蔽
    │
    ├─ video/* ──────────────────────────────────→ [视频处理流水线]
    │                                               TEE 内: 逐帧解码 → 人脸模糊 → 重编码
    │
    └─ application/json / text/* ────────────────→ [半结构化处理流水线]
                                                    结构化解析 → 字段级敏感检测
    │
    ▼
[加密存储]
    • SM4-GCM 加密（块级，Merkle 哈希树）
    • DEK → KMS 托管
    • 元数据（预处理特征向量）→ PostgreSQL
    │
    ▼
[产品目录注册]
    • 写入数据目录，供买方检索
    • 发布脱敏预览（质量报告，不含原始内容）
```

### 4.2 各类型处理流水线技术细节

#### 4.2.1 图像处理流水线（TEE 内）

```python
# 伪代码：TEE 内图像处理流水线
def process_image_in_tee(encrypted_image: bytes, policy: ImagePolicy) -> ProcessedImage:
    # 1. 解密（仅在 TEE 内存中）
    raw_image = sm4_gcm_decrypt(encrypted_image, dek)  # dek 来自 KMS，仅在 TEE 内存在
    
    # 2. 读取图像（JPEG/PNG/DICOM 解码）
    img = decode_image(raw_image)
    
    # 3. 敏感内容检测与脱敏
    if policy.auto_face_redaction:
        faces = detect_faces(img, model="retinaface_r50")  # 模型预加载于 TEE
        for face_bbox in faces:
            img = apply_blur(img, face_bbox, method="gaussian", kernel=51)
    
    if policy.dicom_tag_strip and is_dicom(raw_image):
        img = strip_dicom_tags(img, tags=SENSITIVE_DICOM_TAGS)
    
    if policy.license_plate_redaction:
        plates = detect_license_plates(img)
        for plate_bbox in plates:
            img = apply_mosaic(img, plate_bbox)
    
    # 4. 生成特征向量（可选，用于相似性检索）
    embedding = extract_perceptual_hash(img)  # pHash，不可逆还原原图
    
    # 5. 加密处理后图像存储（供后续计算用）
    processed_encrypted = sm4_gcm_encrypt(encode_image(img), processed_dek)
    
    # 6. 注入溯源水印（隐写，不影响视觉）
    watermarked = inject_invisible_watermark(
        processed_encrypted,
        payload={"session_id": session_id, "ts": timestamp}
    )
    
    return ProcessedImage(
        data=watermarked,
        embedding=embedding,  # 特征向量（密态存储）
        metadata={"redactions": len(faces), "dicom_stripped": True}
    )
```

**关键技术约束**：
- 人脸检测模型在 TEE 内离线运行（模型文件随 TEE 镜像分发，度量值写入 MRENCLAVE）
- DICOM 敏感标签列表（PatientName, PatientID, PatientBirthDate, PatientSex, InstitutionName 等 ≥20 个字段）强制剥离
- 处理后图像不允许输出（仅供 TEE 内算法消费）；输出内容仅限推理结果 JSON

#### 4.2.2 文档处理流水线（TEE 内）

```
PDF/Word 原文（密态）
    │
    ▼ （TEE 内解密）
[结构解析]
    • PDFMiner / python-docx 解析文档结构
    • 提取文本块、表格、图像嵌入物
    │
    ▼
[密态 OCR]（扫描件/图文 PDF）
    • PaddleOCR（轻量版，预置于 TEE 镜像）在密态文档上运行
    • OCR 结果文本仅在 TEE 内存中，不落盘
    │
    ▼
[命名实体识别（NER）]
    • 识别 PII：姓名、电话、身份证、地址、邮箱、银行账号
    • 识别商业敏感信息：金额、日期（合同约束条款）、公司名
    • 模型：轻量级 BERT-NER（中文，≤100MB，预置于 TEE 镜像）
    │
    ▼
[脱敏处理]
    • 按策略：替换（[姓名]→***）/ 泛化（18位身份证→前6位保留后12位掩码）/ 删除
    │
    ▼
[结构化输出]
    仅输出以下内容（经过输出审查网关）：
    • 分类标签（合同类型、风险级别、主题分类）
    • 关键字段摘要（脱敏后，字段长度≤50字符）
    • 向量嵌入（文档语义向量，不可逆还原原文）
    • 拒绝：任何原始段落、表格行、扫描图像片段
```

#### 4.2.3 音频处理流水线（TEE 内）

```
录音文件（WAV/MP3，密态）
    │
    ▼ （TEE 内解密 → 解码为 PCM）
[说话人分离（Speaker Diarization）]
    • 输出：时间段 + 匿名说话人 ID（SPK-001, SPK-002, ...）
    • 使用 pyannote.audio（轻量版，≤200MB）
    │
    ▼
[声纹提取 & 脱敏]
    • 提取声纹向量（d-vector / x-vector）
    • 原始声波不出 TEE；声纹向量可用于说话人识别建模
    │
    ▼
[密态 ASR（自动语音识别）]
    • 使用 Whisper small 或 FunASR（中文优化）
    • 输出文字转录稿（在 TEE 内）
    │
    ▼
[文字转录稿脱敏]
    • NER 识别 PII 并替换
    │
    ▼
[输出]（经输出审查）
    • 脱敏文字转录稿（按合约决定是否允许输出）
    • 情绪分类标签（正/负/中性，逐句）
    • 话题分类标签
    • 说话人交互统计（匿名 SPK-ID 的发言时长/次数）
    禁止：原始录音片段、音频文件、声纹向量
```

#### 4.2.4 视频处理流水线（GPU-TEE / L2 高配）

视频计算量大，优先使用 NVIDIA H100 机密计算（Confidential Computing）模式：

```
视频文件（MP4/AVI，密态，块级存储）
    │
    ▼ （按块解密 + FFmpeg 解码，在 GPU-TEE 内）
[帧级并行处理]（GPU 加速）
    ├─ 人脸检测（YOLO-face，GPU 推理）→ 高斯模糊
    ├─ 车牌检测 → 马赛克
    ├─ 目标检测（行为识别准备）
    │
    ▼
[行为分析]
    • 跌倒检测、聚集行为、特定动作识别
    • 输出：事件标签 + 时间戳（不含任何视频帧）
    │
    ▼
[重编码 & 脱敏帧存储]（仅供 TEE 内算法，不允许输出视频）
    │
    ▼
[输出]（经输出审查）
    • 行为事件时间序列（JSON：{event, timestamp, confidence}）
    • 场景统计（帧级目标计数聚合）
    禁止：视频片段、单帧截图、原始音轨
```

### 4.3 非结构化数据输出审查规则集

```python
class UnstructuredOutputInspector:
    """输出审查网关：非结构化数据专项规则"""

    FORBIDDEN_MIME_PATTERNS = [
        "image/*",       # 禁止任何图像输出（包括 JPEG/PNG/BMP）
        "audio/*",       # 禁止任何音频输出
        "video/*",       # 禁止任何视频输出
        "application/pdf",       # 禁止 PDF 文档输出
        "application/octet-stream",  # 禁止二进制文件输出
    ]

    def inspect(self, output: Any, policy: OutputPolicy) -> InspectionResult:
        # 规则 1：MIME 类型检测
        detected_mime = detect_mime(output)
        if any(fnmatch(detected_mime, pat) for pat in self.FORBIDDEN_MIME_PATTERNS):
            return InspectionResult.BLOCKED(reason="forbidden_mime_type")

        # 规则 2：图像内容检测（即使包装为 Base64 或 JSON 字符串）
        if contains_base64_image(output):
            return InspectionResult.BLOCKED(reason="embedded_image_in_output")

        # 规则 3：PII 二次扫描（防止脱敏逃逸）
        pii_hits = pii_detector.scan(str(output))
        if pii_hits and policy.block_on_pii:
            return InspectionResult.BLOCKED(reason=f"pii_detected: {pii_hits}")

        # 规则 4：体积限制
        if len(str(output).encode()) > policy.max_output_bytes:
            return InspectionResult.BLOCKED(reason="output_size_exceeded")

        # 规则 5：水印注入（所有文字输出）
        if isinstance(output, str) and policy.text_watermark:
            output = inject_text_watermark(output, session_id=self.session_id)

        # 规则 6：差分隐私（聚合数值）
        if policy.differential_privacy and is_numeric_aggregate(output):
            output = add_laplace_noise(output, epsilon=policy.dp_epsilon)

        return InspectionResult.ALLOW(output)
```

---

## 5. 使用控制引擎实现

### 5.1 策略模型（XACML 方言）

```xml
<!-- 示例：医疗影像数据产品策略包 -->
<Policy PolicyId="dicom-analysis-policy-v1">
  <Target>
    <AnyOf>
      <AllOf>
        <Match MatchId="urn:sm:function:string-equal">
          <AttributeValue DataType="string">DICOM_DATASET_001</AttributeValue>
          <AttributeDesignator AttributeId="resource:product-id"/>
        </Match>
      </AllOf>
    </AnyOf>
  </Target>

  <Rule RuleId="allow-classification" Effect="Permit">
    <Condition>
      <Apply FunctionId="urn:sm:function:and">
        <!-- 操作类型限制 -->
        <Apply FunctionId="urn:sm:function:string-at-least-one-member-of">
          <AttributeDesignator AttributeId="action:type"/>
          <Apply FunctionId="urn:sm:function:string-bag">
            <AttributeValue>image_classification</AttributeValue>
            <AttributeValue>object_detection</AttributeValue>
            <AttributeValue>aggregate_statistics</AttributeValue>
          </Apply>
        </Apply>
        <!-- 时间窗口限制 -->
        <Apply FunctionId="urn:sm:function:dateTime-in-range">
          <AttributeDesignator AttributeId="environment:current-dateTime"/>
          <AttributeValue>2025-01-01T00:00:00</AttributeValue>
          <AttributeValue>2025-12-31T23:59:59</AttributeValue>
        </Apply>
        <!-- 输出限制 -->
        <Apply FunctionId="urn:sm:function:integer-less-than">
          <AttributeDesignator AttributeId="action:output-record-count"/>
          <AttributeValue DataType="integer">0</AttributeValue>
          <!-- 输出行数必须为 0（即：仅允许标签/聚合，不允许任何行级输出）-->
        </Apply>
      </Apply>
    </Condition>
  </Rule>

  <Rule RuleId="deny-all" Effect="Deny"/>  <!-- 默认拒绝 -->
</Policy>
```

### 5.2 策略执行引擎（TEE 内运行）

```
策略包（加密下发） → TEE 内解密 → PolicyEngine.load()
                                        │
用户操作请求 ──────────────────────────→ PolicyEngine.evaluate(request)
                                        │
                          ┌─────────────▼──────────────┐
                          │   PDP（策略决策点）           │
                          │   评估优先级：Deny > Permit   │
                          │   默认拒绝（ClosedWorld）     │
                          └─────────────┬──────────────┘
                                        │
                   ┌────────────────────┴─────────────────┐
                   │ Permit                               │ Deny
                   ▼                                      ▼
              PEP 执行操作                       返回拒绝原因码
              记录 ALLOW 日志                    记录 DENY 日志（含规则 ID）
              配额计数器 +1                      若连续拒绝 ≥5 次 → 触发告警
```

### 5.3 实时配额管理

```python
class QuotaManager:
    """
    基于 Redis 的实时配额管理（TEE 外运行，但使用签名令牌防篡改）
    """
    def check_and_consume(self, session_id: str, operation: str, count: int = 1) -> bool:
        key = f"quota:{session_id}:{operation}"
        
        # Lua 脚本原子检查+扣减（防竞态）
        script = """
        local current = redis.call('GET', KEYS[1])
        if current == false then return -1 end  -- 配额未初始化
        if tonumber(current) < tonumber(ARGV[1]) then return 0 end  -- 超限
        redis.call('DECRBY', KEYS[1], ARGV[1])
        return 1  -- 成功
        """
        result = redis.eval(script, 1, key, count)
        
        # 结果用 SM2 私钥签名后传入 TEE 验证，防止配额结果被篡改
        signed_result = sm2_sign(str(result), kms_private_key)
        return result == 1
```

---

## 6. 输出审查网关设计

### 6.1 审查流水线

```
TEE 内计算结果（内存缓冲区）
    │
    ▼
① 格式验证
    • 检测是否为允许的输出格式（JSON/CSV 统计/文字/标签）
    • 拒绝：图像二进制、音频流、原始行级数据

    ▼
② 内容安全扫描（NLP + 规则混合）
    • PII 检测（姓名/手机/身份证/银行卡正则 + NER 模型双层）
    • 企业机密信息检测（商业秘密特征词库）
    • 原始数据重建检测（字段精确匹配率 > 阈值 → 拒绝）

    ▼
③ 差分隐私处理（仅数值聚合结果）
    • 全局敏感度分析（Δf 估算）
    • Laplace 噪声注入：noise ~ Lap(Δf / ε)
    • 若单次查询信息量过大（ε 预算耗尽） → 拒绝

    ▼
④ 输出量限制
    • 行数计数器校验（≤ policy.maxOutputRows）
    • 字节大小校验（≤ policy.maxOutputBytes）

    ▼
⑤ 溯源水印注入
    • 文字输出：零宽字符隐写（Unicode ZWSP/ZWJ/ZWNBSP 编码 session_id 后 4 字节）
    • 数值结果：LSB 编码（最低有效位嵌入溯源标识，对结果精度影响 < 0.001%）

    ▼
⑥ 签名输出
    • SM2 私钥对结果签名（沙箱节点私钥）
    • 签名随结果一起返回买方，供事后验真

    ▼
合规输出（TLCP 加密传输 → 买方）
```

### 6.2 差分隐私预算管理

```
每个合约维护一个 ε 预算账本：

contract.dpBudget.total = ε_total          # 合约总预算，由数商设定
contract.dpBudget.consumed = 0             # 已消耗预算

每次查询：
    ε_cost = estimate_query_sensitivity(query)  # 基于查询类型估算本次 ε 消耗
    if consumed + ε_cost > total:
        → 拒绝查询，提示预算耗尽
    else:
        → 执行查询，注入 Laplace(Δf / ε_cost) 噪声
        → consumed += ε_cost
        → 写入审计日志（ε_cost, consumed, total）

预算耗尽处理：
    • 买方可申请续约（需数商重新签合约，重置预算）
    • 预算耗尽不影响历史结果有效性
```

---

## 7. 互联互通技术实现

### 7.1 跨空间身份互认协议

```
空间 A（含 CA-A）                    空间 B（含 CA-B）
     │                                     │
     │  1. 空间联盟根 CA 签发互信证书         │
     │ ←─────── Root CA 签名 ──────────────→│
     │                                     │
买方（空间B成员）   ──请求访问空间A数据→    空间A连接器
     │                                     │
     │  2. 提交 SM2 证书（CA-B 签发）        │
     │ ──────────────────────────────────→ │
     │                                     │ 3. 验证证书链：
     │                                     │   CA-B cert → Root CA
     │                                     │   （Root CA 在 A 的信任存储中）
     │  4. 返回跨空间临时访问令牌（JWT，SM2 签名）
     │ ←──────────────────────────────────  │
     │                                     │
     │  5. 携带令牌创建沙箱会话              │
     │ ──────────────────────────────────→ │（验证令牌 + 合约）
```

### 7.2 连接器标准接口实现规范

所有接口基于 HTTPS + TLCP（国密 TLS），请求/响应体使用 JSON。

#### 核心接口定义

```typescript
// POST /sandbox/session/create
interface CreateSessionRequest {
  contractId: string;           // 数字合约 ID
  buyerCertPem: string;         // 买方 SM2 证书（PEM）
  buyerSignature: string;       // 买方对 contractId+timestamp 的 SM2 签名
  timestamp: number;            // Unix 毫秒时间戳（防重放，有效期 300s）
  requestedLevel?: "L1" | "L2"; // 请求安全等级（数商策略限制最低等级）
  crossSpaceToken?: string;     // 跨空间令牌（跨空间场景使用）
}

interface CreateSessionResponse {
  sessionId: string;
  actualLevel: "L1" | "L2" | "L3";       // 实际分配的安全等级
  attestationReport?: string;             // TEE 证明报告（L1 时提供）
  softwareAttestation?: {                 // 软件证明（L2 时提供）
    envHash: string;                      // 环境镜像 SM3 哈希
    policyHash: string;                   // 策略包 SM3 哈希
    endorsement?: string;                 // 第三方背书证书（可选）
  };
  expiresAt: number;                      // 会话过期时间（Unix 毫秒）
}

// POST /sandbox/task/submit
interface SubmitTaskRequest {
  sessionId: string;
  taskType: "sql" | "python" | "inference" | "fl_round";
  code?: string;                          // 代码（SQL/Python），Base64 编码
  modelId?: string;                       // 推理任务使用预注册模型 ID
  codeSignature: string;                  // 买方对 code 的 SM2 签名（防篡改）
  params?: Record<string, any>;           // 任务参数
}

interface SubmitTaskResponse {
  taskId: string;
  status: "queued" | "running" | "completed" | "failed";
  estimatedSeconds?: number;
}

// GET /sandbox/task/result?taskId=xxx
interface TaskResultResponse {
  taskId: string;
  status: "completed" | "failed" | "pending";
  result?: any;                           // 经审查的计算结果
  resultSignature?: string;              // 沙箱节点 SM2 签名（可供买方验真）
  dpEpsilonConsumed?: number;            // 本次任务消耗的 ε 预算
  dpEpsilonRemaining?: number;           // 合约剩余 ε 预算
  auditEventId?: string;                 // 对应审计事件 ID（可查链上存证）
  error?: string;
}

// GET /sandbox/attest?sessionId=xxx
interface AttestationResponse {
  level: "L1" | "L2";
  report: string;                        // TEE Quote（L1）或软件证明 JSON（L2）
  signature: string;                     // SM2 签名
  validUntil: number;
}
```

### 7.3 跨空间数据产品目录协议

遵循 NDI-TR-2025-02，目录条目格式（扩展非结构化字段）：

```json
{
  "productId": "dp-001",
  "spaceId": "space-gov-healthdata",
  "name": "某省医疗影像数据集 2024",
  "category": "medical/imaging",
  "dataType": "unstructured",
  "mediaType": "image/dicom",
  "volume": {"fileCount": 50000, "totalSizeGB": 800},
  "schema": null,
  "unstructuredMeta": {
    "modality": "CT",
    "bodyPart": "chest",
    "autoRedaction": ["face", "dicom_patient_tags"],
    "allowedOutputFormats": ["classification_label", "embedding_vector"]
  },
  "securityLevelRequired": "L1",
  "contractTemplate": "dicom-analysis-v2",
  "qualityReport": {
    "sampleSize": 100,
    "completenessRate": 0.98,
    "previewEmbedding": "[0.23, -0.11, ...]"
  },
  "certifications": ["TC609-2025", "CMIA-医疗数据安全认证"],
  "pricing": {
    "model": "per_task",
    "currency": "CNY",
    "taskPrice": 500
  }
}
```

---

## 8. 存证审计系统设计

### 8.1 审计日志架构

```
TEE 沙箱内                                         TEE 外
─────────────────────────────                    ─────────────────────
操作发生
    │
    ▼
PolicyEngine 判定 → 生成审计事件（结构化 JSON）
    │
    │ SM2 签名（用沙箱私钥，防止 TEE 外篡改）
    ▼
[TEE 外：日志收集器]
    │
    ├─→ ClickHouse（实时写入，细粒度存储，保留 6 个月+）
    │       查询服务：支持按 session_id / product_id / actor 检索
    │
    └─→ 上链流水线
            │ 批量聚合（每 1000 条或每 30 秒）
            ▼
        SM3(batch_events) → 生成 Merkle 根哈希
            │
            ▼
        FISCO BCOS 合约调用：
        AuditRegistry.submitBatch(merkleRoot, batchId, timestamp)
            │
            ▼
        链上存证（不可篡改，永久保存摘要）
        实际日志内容保留在 ClickHouse，上链只存摘要
```

### 8.2 区块链存证合约（Solidity-like，FISCO BCOS）

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract SandboxAuditRegistry {
    struct AuditBatch {
        bytes32 merkleRoot;      // 一批审计日志的 SM3 Merkle 根
        uint256 batchId;
        uint256 timestamp;
        address submitter;       // 提交节点地址
        string spaceId;          // 所属数据空间 ID
    }

    mapping(uint256 => AuditBatch) public batches;
    uint256 public batchCount;

    event BatchSubmitted(uint256 indexed batchId, bytes32 merkleRoot, uint256 timestamp);

    function submitBatch(
        bytes32 _merkleRoot,
        uint256 _batchId,
        string memory _spaceId
    ) external onlyAuthorizedNode {
        batches[_batchId] = AuditBatch({
            merkleRoot: _merkleRoot,
            batchId: _batchId,
            timestamp: block.timestamp,
            submitter: msg.sender,
            spaceId: _spaceId
        });
        batchCount++;
        emit BatchSubmitted(_batchId, _merkleRoot, block.timestamp);
    }

    // 验真接口：给定日志条目，验证其是否在指定批次的 Merkle 树中
    function verifyAuditEntry(
        uint256 _batchId,
        bytes32 _entryHash,
        bytes32[] memory _merkleProof
    ) external view returns (bool) {
        bytes32 root = batches[_batchId].merkleRoot;
        return MerkleProof.verify(_merkleProof, root, _entryHash);
    }
}
```

---

## 9. 安全边界与威胁模型

### 9.1 威胁建模（STRIDE）

| 威胁类型 | 具体威胁 | 对抗措施 |
|----------|----------|----------|
| **S**poofing（欺骗） | 伪造数商/买方身份 | SM2 证书 + 多因素认证；跨空间令牌时间戳防重放 |
| **T**ampering（篡改） | 修改策略包、审计日志 | 策略包 SM3 哈希锁定；日志 SM2 签名；链上存证 |
| **R**epudiation（抵赖） | 否认执行过某操作 | 链上不可篡改存证；操作日志包含 SM2 签名 |
| **I**nformation Disclosure（信息泄露） | 数据从 TEE 内泄露 | 硬件内存加密；输出审查；差分隐私；禁止原文输出 |
| **D**enial of Service（拒绝服务） | 消耗 TEE 资源使正常任务无法运行 | 任务配额；资源 cgroup 限制；任务超时熔断 |
| **E**levation of Privilege（权限提升） | 沙箱逃逸，访问宿主机数据 | TEE 硬件隔离；gVisor 用户态内核；Seccomp-BPF |

### 9.2 侧信道攻击防护（L1 TEE 专项）

| 攻击类型 | 防护措施 |
|----------|----------|
| 缓存侧信道（Spectre/Meltdown） | Occlum 内置缓存分区；禁用 hyperthreading（per-task 独占物理核） |
| 内存访问模式泄露 | ORAM（Oblivious RAM）用于敏感数据随机化访问模式（高安全场景选用） |
| 功耗侧信道 | 云环境下 Intel 已缓解；物理机部署需配合电磁屏蔽机房 |
| 时序侧信道 | 敏感比较操作使用恒定时间算法（constant-time SM2 实现） |

### 9.3 L2 特有安全约束

L2 无法防御以下威胁（需在合约中告知买方）：

- **宿主机操作系统级攻击**：若宿主机 OS 被 root 级攻陷，gVisor 沙箱可能被绕过
- **内存 dump**：无硬件内存加密，物理内存 dump 后可能获取解密中的数据（缓解：dm-crypt + 短生命周期密钥）
- **内核漏洞利用**：gVisor 的用户态内核本身存在漏洞风险（缓解：定期更新 + 漏洞扫描）

**L2 应用限制**：仅用于一般敏感数据（非《数据安全法》定义的重要数据或核心数据）。

---

## 10. 部署架构

### 10.1 单空间部署（最小生产配置）

```
┌─────────────────────────────────────────────────────┐
│                  可信数据空间 A                        │
│                                                     │
│  ┌────────────────────┐  ┌──────────────────────┐   │
│  │    控制平面节点      │  │    TEE 计算节点（L1）  │   │
│  │  2C8G（常规服务器） │  │  Intel Xeon SGX 服务器│   │
│  │                    │  │  EPC 内存：256GB+     │   │
│  │  • API 网关         │  │  • Occlum 运行时      │   │
│  │  • 合约服务         │  │  • DCAP 证明服务      │   │
│  │  • 密钥服务(KMS)    │  │  • 任务执行容器池     │   │
│  │  • 任务调度器       │  │                      │   │
│  │  • 审计服务         │  └──────────────────────┘   │
│  └────────────────────┘                             │
│                                                     │
│  ┌────────────────────┐  ┌──────────────────────┐   │
│  │    存储节点         │  │  L2 降级计算节点       │   │
│  │  • MinIO（加密）    │  │  普通 x86 服务器       │   │
│  │  • PostgreSQL       │  │  • Firecracker + gVisor│  │
│  │  • Redis（配额）    │  │  • eBPF 监控          │   │
│  └────────────────────┘  └──────────────────────┘   │
│                                                     │
│  ┌────────────────────┐                             │
│  │    区块链节点        │                             │
│  │  • FISCO BCOS      │                             │
│  │  • 存证合约         │                             │
│  └────────────────────┘                             │
└─────────────────────────────────────────────────────┘
```

### 10.2 跨空间互联互通部署

```
空间 A（金融数据空间）              空间 B（政务数据空间）
┌─────────────────────┐          ┌─────────────────────┐
│  连接器 A           │          │  连接器 B            │
│  • 服务平台适配      │          │  • 服务平台适配       │
│  • 身份转换层        │ ←──────→ │  • 身份转换层         │
│  • 数据目录代理      │  TLCP    │  • 数据目录代理       │
└──────────┬──────────┘          └──────────┬──────────┘
           │                                │
           └──────────┬─────────────────────┘
                      │
           ┌──────────▼──────────┐
           │  密态沙箱（CDS）     │
           │  （可部署于任意侧）  │
           │  L1 节点（跨空间     │
           │  互联互通强制 L1）   │
           └─────────────────────┘
```

### 10.3 关键配置参数

```yaml
# sandbox-config.yaml
sandbox:
  runtime:
    l1:
      enabled: true
      teeFramework: "occlum"          # occlum | hyperenclave | itrustee
      epcMemoryGB: 64                 # 单沙箱 EPC 内存分配
      maxConcurrentSessions: 20       # 单节点最大并发会话
      attestationService: "dcap"      # dcap | itrustee | csv
    l2:
      enabled: true
      vmRuntime: "firecracker"        # firecracker | qemu-kvm
      containerRuntime: "gvisor"      # gvisor | runc
      maxConcurrentSessions: 50
    l3:
      enabled: false                  # 生产环境默认关闭
      allowedDataLevels: ["public", "low_sensitivity"]

  policy:
    defaultDenyAll: true              # 策略默认拒绝
    dpDefaultEpsilon: 1.0             # L1 默认 ε
    dpL2MaxEpsilon: 0.5               # L2 最大允许 ε
    outputMaxRows: 100                # L1 最大输出行数
    outputMaxRowsL2: 20               # L2 最大输出行数

  unstructured:
    enableFaceRedaction: true
    faceRedactionModel: "retinaface_r50_quantized"
    enableDicomTagStrip: true
    enableAsrTranscription: true
    asrModel: "funASR_paraformer_zh"  # 中文优化 ASR
    forbiddenOutputMimes:
      - "image/*"
      - "audio/*"
      - "video/*"

  audit:
    localRetentionDays: 180           # 本地审计日志保留 180 天
    blockchainNetwork: "fisco-bcos"
    batchSize: 1000
    batchIntervalSeconds: 30

  crypto:
    preferGmAlgorithms: true          # 优先使用国密算法
    sm4KeyLengthBits: 128
    hsmEnabled: true
    hsmProvider: "as2805"             # 国产 HSM 型号
```

---

## 11. 性能基准与优化

### 11.1 性能目标

| 操作 | L1 TEE | L2 软件 | 基准环境 |
|------|--------|---------|----------|
| 沙箱启动（含密钥分发） | ≤60s (P99) | ≤15s (P99) | SGX 服务器 / 普通 x86 |
| SQL 聚合查询（1亿行） | ≤120s | ≤60s | 单节点 32C128G |
| Python 逻辑回归训练（100万样本） | ≤600s | ≤300s | 同上 |
| 图像脱敏预处理（1000张 DICOM） | ≤300s | ≤180s | 含 GPU 加速（H100） |
| 音频 ASR 转录（1小时录音） | ≤600s | ≤300s | 纯 CPU |
| 输出审查延迟 | ≤500ms | ≤200ms | 1MB 文本结果 |
| 跨空间会话建立 | ≤5s | ≤3s | 同城网络 |

### 11.2 关键优化策略

| 优化方向 | 措施 |
|----------|------|
| TEE 内存优化 | Occlum 异步 I/O；分块数据流式加载（避免 EPC Paging 抖动） |
| 并发沙箱调度 | 按数据产品预热热门 Enclave（模型预加载）；任务拼包（batch inference）|
| 非结构化大文件 | 分块并行处理（多线程解密 + 流水线预处理）；GPU-TEE 加速图像/视频 |
| L2 MPC 通信 | 预计算乘法三元组（离线生成，online 阶段仅做 O(1) 通信）|
| 审计日志 | 异步写入（任务结束后 30s 内完成上链，不阻塞结果返回）|

---

## 12. 测试与质量保障

### 12.1 测试矩阵

| 测试类型 | 测试内容 | 工具 |
|----------|----------|------|
| 功能测试 | 各类沙箱类型任务提交/执行/输出 | pytest + 自定义 SDK |
| 安全测试 | 沙箱逃逸尝试（CVE 复现集）；PII 泄露尝试 | 红队测试 + OWASP ZAP |
| 策略测试 | 策略执行正确性（允许/拒绝决策验证） | 基于 XACML 测试套件 |
| 差分隐私测试 | ε 预算扣减正确性；噪声分布验证 | 差分隐私审计库（opacus dp-accounting）|
| 互联互通测试 | 跨空间会话建立；证书互认；合约绑定 | 集成测试环境（两套数据空间）|
| 性能测试 | 并发会话压测；大文件处理吞吐 | Locust + 自定义 TEE 压测框架 |
| 合规测试 | TC609-6-2025-01 接口合规验证 | 标委会提供的互操作测试套件 |

### 12.2 安全认证目标

| 认证 | 标准 | 计划周期 |
|------|------|----------|
| 信息系统安全等级保护三级 | GB/T 22239-2019 | Phase 1 完成后 6 个月内 |
| 商用密码应用安全性评估（密评） | GM/T 0054-2018 | Phase 1 完成后 |
| TC609 可信数据空间互操作认证 | TC609-6-2025-01 | Phase 2 完成后 |
| 数据安全能力成熟度（DSMM）三级 | GB/T 37988-2019 | Phase 3 完成后 |

---

*文档版本：v1.0 | 状态：草案 | 受众：研发/安全/运维 | 对应产品规格：product-spec v1.1*
