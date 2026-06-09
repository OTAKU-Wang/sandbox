# SS-07：AI 大模型训练安全流水线 分系统技术规格

> 版本：v1.0 | 分系统编号：SS-07  
> 覆盖场景：LLM 预训练/SFT、视觉模型、多模态模型、联邦学习  
> 上游：SS-04（沙箱运行时提供计算环境）  
> 下游：SS-05（模型导出审查）、SS-06（训练过程审计）

---

## 1. 训练安全威胁模型

| 威胁 | 攻击路径 | 对抗手段 |
|------|----------|----------|
| **训练数据提取** | 模型记忆训练样本，推理时复现 | MIA 探针 + 文本记忆检测 + 差分隐私训练 |
| **梯度反演** | 联邦场景中，服务端从梯度反推本地数据 | 梯度裁剪 + DP 噪声 + 安全聚合 |
| **模型窃取** | checkpoint 泄露导致数商数据特征可被推断 | checkpoint 加密存储 + 模型水印 |
| **后门植入** | 买方上传含触发器的训练代码 | 代码静态扫描 + 训练行为基线检测 |
| **显存/内存 dump** | 攻击者 dump GPU 显存获取批次明文 | GPU-TEE（NVIDIA H100 CC 模式）|
| **超参数注入** | 通过超参数触发异常训练行为 | 超参数白名单 + 范围校验 |

---

## 2. 分布式训练安全架构

### 2.1 多卡分布式训练（DDP/FSDP）

大模型训练通常跨多 GPU 甚至多节点。所有参与训练的节点均须在 TEE 保护范围内：

```
分布式训练节点拓扑（8卡单机 DDP）：

  CPU-TEE（Occlum/SGX）
  ┌──────────────────────────────────────────────────────────┐
  │  训练主进程（Rank 0）                                      │
  │  ・DataLoader：批次解密 + 脱敏                             │
  │  ・梯度聚合（DDP AllReduce）                               │
  │  ・checkpoint 加密保存                                     │
  └────────────────────┬─────────────────────────────────────┘
                       │ NVLink / PCIe（CC 加密通道）
  ┌────────────────────▼─────────────────────────────────────┐
  │  GPU-TEE 区（NVIDIA H100 × 8，CC 模式）                   │
  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                   │
  │  │GPU:0 │ │GPU:1 │ │GPU:2 │ │GPU:3 │   ← 前向/反向传播  │
  │  └──────┘ └──────┘ └──────┘ └──────┘                   │
  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                   │
  │  │GPU:4 │ │GPU:5 │ │GPU:6 │ │GPU:7 │   ← FSDP 分片     │
  │  └──────┘ └──────┘ └──────┘ └──────┘                   │
  │  NVLink Switch（GPU 间高速互联，CC 模式下通信加密）         │
  └──────────────────────────────────────────────────────────┘
```

```python
class SecureDistributedTrainer:
    """
    安全分布式训练协调器
    支持：DDP（数据并行）/ FSDP（全分片数据并行）/ 张量并行
    """

    def setup_distributed(self, config: DistributedConfig):
        # 1. 初始化进程组（NCCL 通信，GPU-TEE 内加密）
        dist.init_process_group(
            backend="nccl",
            init_method="env://",
            world_size=config.world_size,
            rank=config.rank,
        )

        # 2. 验证所有节点的 GPU CC 状态
        for rank in range(config.world_size):
            if rank == dist.get_rank():
                cc_status = nvidia_cc.verify_current_device()
                assert cc_status.enabled, f"Rank {rank} GPU CC not enabled"
                # 广播当前节点 GPU 证明报告（其他节点验证）
                self._broadcast_gpu_attestation(rank)

        # 3. 验证接收到的其他节点证明报告
        self._verify_peer_attestations(config.world_size)

    def _broadcast_gpu_attestation(self, rank: int):
        """广播本节点 GPU 证明报告，供其他节点验证"""
        report = nvidia_cc.get_attestation_report()
        report_tensor = torch.tensor(
            list(report.to_bytes()), dtype=torch.uint8
        )
        dist.broadcast(report_tensor, src=rank)

    def wrap_model_for_distributed(
        self, model: nn.Module, config: DistributedConfig
    ) -> nn.Module:
        if config.strategy == "ddp":
            return DDP(
                model,
                device_ids=[config.local_rank],
                find_unused_parameters=False,
                gradient_as_bucket_view=True,
            )
        elif config.strategy == "fsdp":
            from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
            from torch.distributed.fsdp.wrap import transformer_auto_wrap_policy
            return FSDP(
                model,
                auto_wrap_policy=transformer_auto_wrap_policy,
                sharding_strategy=ShardingStrategy.FULL_SHARD,
                mixed_precision=MixedPrecision(
                    param_dtype=torch.bfloat16,
                    reduce_dtype=torch.float32,
                ),
                device_id=config.local_rank,
            )
```

### 2.2 差分隐私训练（DP-SGD）

```python
class DPSGDTrainer:
    """
    差分隐私随机梯度下降训练器
    基于 Opacus 框架（支持 PyTorch）
    ε 预算管理对接 SS-02 合约预算账本
    """

    def __init__(
        self,
        model: nn.Module,
        dataloader: SecureDataLoader,
        dp_config: DPConfig,
        contract: Contract,
    ):
        from opacus import PrivacyEngine
        from opacus.validators import ModuleValidator

        # 验证模型结构兼容 DP-SGD
        errors = ModuleValidator.validate(model, strict=False)
        if errors:
            model = ModuleValidator.fix(model)

        self.optimizer = torch.optim.AdamW(model.parameters(), lr=dp_config.lr)
        self.privacy_engine = PrivacyEngine()

        # 将 DP 约束绑定到 optimizer
        self.model, self.optimizer, self.dp_dataloader = (
            self.privacy_engine.make_private_with_epsilon(
                module=model,
                optimizer=self.optimizer,
                data_loader=dataloader,
                epochs=dp_config.num_epochs,
                target_epsilon=dp_config.target_epsilon,
                target_delta=dp_config.target_delta,
                max_grad_norm=dp_config.max_grad_norm,  # 梯度裁剪范数
            )
        )

        self.contract = contract
        self._validate_dp_budget_sufficient(dp_config)

    def _validate_dp_budget_sufficient(self, config: DPConfig):
        """
        验证合约 DP 预算是否足够完成计划的训练
        """
        estimated_epsilon = config.target_epsilon  # 训练结束时的总 ε 消耗
        if self.contract.dp_budget_remaining < estimated_epsilon:
            raise InsufficientDPBudget(
                f"Training requires ε={estimated_epsilon:.2f}, "
                f"but only ε={self.contract.dp_budget_remaining:.2f} remaining"
            )

    def train_epoch(self, epoch: int) -> EpochMetrics:
        self.model.train()
        total_loss = 0.0

        for batch in self.dp_dataloader:
            self.optimizer.zero_grad()
            loss = self.model(**batch).loss
            loss.backward()

            # Opacus 在 optimizer.step() 内部自动执行：
            # 1. 每个样本的梯度裁剪（L2 norm ≤ max_grad_norm）
            # 2. 梯度加噪（Gaussian 机制）
            # 3. 聚合并更新参数
            self.optimizer.step()
            total_loss += loss.item()

        # 查询当前 ε 消耗（Opacus 内置 RDP 会计）
        epsilon_consumed = self.privacy_engine.get_epsilon(delta=self.contract.dp_delta)
        self._sync_epsilon_to_contract(epsilon_consumed)

        return EpochMetrics(
            epoch=epoch,
            loss=total_loss / len(self.dp_dataloader),
            epsilon_consumed=epsilon_consumed,
            delta=self.contract.dp_delta,
        )

    def _sync_epsilon_to_contract(self, total_epsilon: float):
        """将实际消耗的 ε 同步回 SS-02 合约预算账本"""
        incremental = total_epsilon - self._last_synced_epsilon
        if incremental > 0:
            contract_service.charge_dp_budget(
                contract_id=self.contract.contract_id,
                epsilon=incremental,
                source="dp_sgd_training",
            )
        self._last_synced_epsilon = total_epsilon
```

---

## 3. 联邦学习安全协议

### 3.1 跨数商联合训练架构

多个数商各持有本地数据，在沙箱内进行联邦学习，无需汇聚原始数据：

```
联邦学习架构（横向联邦，数商 A + 数商 B）：

  数商 A 沙箱（TEE-L1）           数商 B 沙箱（TEE-L1）
  ┌───────────────────────┐       ┌───────────────────────┐
  │ 本地数据（A 私有）     │       │ 本地数据（B 私有）     │
  │ ↓ 本地训练             │       │ ↓ 本地训练             │
  │ 本地梯度               │       │ 本地梯度               │
  │ ↓ DP 裁剪 + 加噪       │       │ ↓ DP 裁剪 + 加噪       │
  │ ↓ 安全聚合加密         │       │ ↓ 安全聚合加密         │
  └──────────┬────────────┘       └──────────┬────────────┘
             │  加密梯度分片                   │  加密梯度分片
             └─────────────┬──────────────────┘
                           ▼
              聚合服务器（CDS 平台，无法看到明文梯度）
              ┌──────────────────────────────────┐
              │ 安全聚合（SecAgg 协议）            │
              │ 解密并求和（服务端仅见聚合结果）   │
              └──────────────┬───────────────────┘
                             ▼
              更新后的全局模型权重（下发所有参与方）
```

### 3.2 安全聚合（SecAgg）实现

```python
class SecureAggregationProtocol:
    """
    安全梯度聚合：服务端无法获取任何单个参与方的梯度
    基于 Bonawitz et al. SecAgg 协议的简化两方版本
    """

    def __init__(self, participants: list[str], threshold: int):
        self.participants = participants
        self.threshold = threshold          # 容忍 dropout 的最小参与方数
        self.round_keys = {}

    def client_mask_gradients(
        self,
        local_gradients: dict[str, torch.Tensor],
        client_id: str,
        round_id: int,
    ) -> dict[str, bytes]:
        """
        客户端（数商沙箱）：对本地梯度添加随机掩码
        每对参与方之间协商一个随机种子，生成配对掩码
        """
        masked_grads = {}
        for param_name, grad in local_gradients.items():
            flat = grad.flatten().float()
            mask = torch.zeros_like(flat)

            # 对每个其他参与方添加/减去配对掩码
            for other_id in self.participants:
                if other_id == client_id:
                    continue
                seed = self._get_pair_seed(client_id, other_id, round_id)
                torch.manual_seed(seed)
                pair_mask = torch.randn_like(flat) * MASK_SCALE

                if client_id < other_id:
                    mask += pair_mask    # A 加上，B 减去，聚合后掩码抵消
                else:
                    mask -= pair_mask

            masked_grads[param_name] = sm4_gcm_encrypt(
                (flat + mask).numpy().tobytes(),
                self.round_keys[round_id]
            )

        return masked_grads

    def server_aggregate(
        self,
        all_masked_grads: dict[str, dict[str, bytes]],  # {client_id: {param: masked_bytes}}
        round_id: int,
    ) -> dict[str, torch.Tensor]:
        """
        服务端聚合：解密各方掩码梯度并求和
        由于掩码两两抵消，聚合结果 = 真实梯度之和（不含任何单方信息）
        """
        aggregated = {}
        for param_name in next(iter(all_masked_grads.values())).keys():
            param_sum = None
            for client_id, grads in all_masked_grads.items():
                decrypted = sm4_gcm_decrypt(
                    grads[param_name], self.round_keys[round_id])
                grad_tensor = torch.frombuffer(decrypted, dtype=torch.float32)
                param_sum = grad_tensor if param_sum is None else param_sum + grad_tensor

            # 求平均
            aggregated[param_name] = param_sum / len(all_masked_grads)

        return aggregated
```

---

## 4. Checkpoint 安全管理

```python
class SecureCheckpointManager:
    """
    训练过程中的模型 checkpoint 安全管理
    Checkpoint 文件全程加密存储，仅在沙箱内可解密使用
    """

    CHECKPOINT_DIR = "/sandbox/checkpoints/"

    def save_checkpoint(
        self,
        model: nn.Module,
        optimizer: torch.optim.Optimizer,
        epoch: int,
        metrics: dict,
        session: SandboxSession,
    ):
        checkpoint_path = f"{self.CHECKPOINT_DIR}/epoch_{epoch:04d}/"
        os.makedirs(checkpoint_path, exist_ok=True)

        # 1. 序列化模型权重（safetensors 格式，比 pickle 更安全）
        from safetensors.torch import save_file
        state_dict = model.state_dict()
        safetensors_path = f"{checkpoint_path}/model.safetensors"
        save_file(state_dict, safetensors_path)

        # 2. SM4-GCM 加密（使用合约 DEK，仅当前沙箱可解密）
        with open(safetensors_path, "rb") as f:
            plaintext = f.read()
        iv = os.urandom(12)
        ciphertext, auth_tag = sm4_gcm_encrypt(plaintext, session.dek, iv)

        with open(f"{checkpoint_path}/model.safetensors.enc", "wb") as f:
            f.write(iv + auth_tag + ciphertext)
        os.remove(safetensors_path)  # 删除明文文件

        # 3. 保存元数据（不含模型权重）
        meta = {
            "epoch": epoch,
            "metrics": metrics,
            "timestamp": datetime.utcnow().isoformat(),
            "model_hash_sm3": sm3_hash(plaintext).hex(),
            "session_id": session.session_id,
            "contract_id": session.contract_id,
        }
        with open(f"{checkpoint_path}/meta.json", "w") as f:
            json.dump(meta, f)

        audit_service.record("train.checkpoint.saved",
                              epoch=epoch, path=checkpoint_path)

    def load_checkpoint(self, epoch: int, session: SandboxSession) -> dict:
        """从加密 checkpoint 恢复模型状态"""
        checkpoint_path = f"{self.CHECKPOINT_DIR}/epoch_{epoch:04d}/"
        enc_path = f"{checkpoint_path}/model.safetensors.enc"

        with open(enc_path, "rb") as f:
            raw = f.read()

        iv = raw[:12]
        auth_tag = raw[12:28]
        ciphertext = raw[28:]

        plaintext = sm4_gcm_decrypt(ciphertext, session.dek, iv, auth_tag)

        # 完整性校验
        meta = json.load(open(f"{checkpoint_path}/meta.json"))
        assert sm3_hash(plaintext).hex() == meta["model_hash_sm3"], "Checkpoint tampered"

        # 写入临时文件（仅在内存文件系统）
        tmp_path = f"/dev/shm/checkpoint_tmp_{session.session_id}.safetensors"
        with open(tmp_path, "wb") as f:
            f.write(plaintext)

        from safetensors.torch import load_file
        state_dict = load_file(tmp_path)
        os.remove(tmp_path)  # 立即删除

        return state_dict

    def cleanup_checkpoints(self, keep_last_n: int = 2):
        """清理旧 checkpoint（只保留最近 N 个）"""
        checkpoints = sorted(
            [d for d in os.listdir(self.CHECKPOINT_DIR) if d.startswith("epoch_")]
        )
        for old_ckpt in checkpoints[:-keep_last_n]:
            old_path = f"{self.CHECKPOINT_DIR}/{old_ckpt}"
            for f in os.listdir(old_path):
                subprocess.run(["shred", "-u", "-n", "1",
                                 f"{old_path}/{f}"])
            os.rmdir(old_path)
```

---

## 5. 模型记忆性检测完整套件

```python
class ModelMemorizationSuite:
    """
    训练完成后的综合记忆性检测
    包含：MIA / 文本提取 / 图像重建 / 训练数据成员测试
    """

    def run_full_assessment(
        self, model, dataloader: SecureDataLoader, model_type: str
    ) -> MemorizationReport:

        report = MemorizationReport(model_type=model_type)

        # 1. MIA（成员推断攻击）——所有类型通用
        report.mia_advantage = self._mia_shadow_attack(model, dataloader)

        # 2. 文本提取测试（LLM 专用）
        if model_type in ("llm_sft", "llm_pretrain"):
            report.text_extraction_rate = self._test_text_extraction(
                model, dataloader, n_prompts=200
            )
            report.verbatim_memorization_k50 = self._count_verbatim_k(
                model, dataloader, k=50
            )

        # 3. 图像重建测试（视觉/多模态专用）
        if model_type in ("vision_train", "multimodal_train"):
            report.image_reconstruction_score = self._test_image_reconstruction(
                model, dataloader
            )

        # 4. 梯度归因测试（检测是否通过梯度可还原训练样本）
        if model_type == "llm_pretrain":
            report.gradient_attribution_risk = self._gradient_attribution_test(
                model, dataloader, n_samples=50
            )

        # 判定是否通过
        report.passed = (
            report.mia_advantage <= THRESHOLDS["mia_advantage"] and
            (model_type not in ("llm_sft", "llm_pretrain") or
             report.text_extraction_rate <= THRESHOLDS["text_extraction"]) and
            (model_type not in ("vision_train", "multimodal_train") or
             report.image_reconstruction_score <= THRESHOLDS["image_reconstruction"])
        )

        return report

    def _count_verbatim_k(
        self, model, dataloader, k: int, n_samples: int = 500
    ) -> float:
        """
        统计模型逐字记忆率（k-extractable memorization）
        给定 k 个 token 前缀，模型能否生成后续连续 50 个完全匹配的 token
        """
        prefix_suffix_pairs = dataloader.get_prefix_samples(
            n=n_samples, prefix_len=k, suffix_len=50
        )
        verbatim_count = 0
        for prefix, true_suffix in prefix_suffix_pairs:
            generated = model.generate(
                prefix, max_new_tokens=50,
                do_sample=False,        # 贪婪解码
                temperature=1.0,
            )
            if generated == true_suffix:  # 完全匹配
                verbatim_count += 1
        return verbatim_count / n_samples

THRESHOLDS = {
    "mia_advantage": 0.15,       # MIA advantage ≤ 15%
    "text_extraction": 0.03,     # 文本提取率 ≤ 3%
    "image_reconstruction": 0.2, # 图像重建相似度 ≤ 0.2（CLIP 得分）
    "gradient_attribution": 0.1, # 梯度归因风险 ≤ 10%
}
```

---

## 6. 训练超参数安全校验

```python
class TrainingConfigValidator:
    """
    训练配置（超参数）安全校验
    防止：通过异常超参数触发后门/过拟合/记忆性风险
    """

    PARAM_CONSTRAINTS = {
        "llm_sft": {
            "learning_rate":        (1e-6, 1e-3),
            "num_epochs":           (1, 10),
            "batch_size":           (1, 128),
            "max_seq_len":          (128, 8192),
            "lora_r":               (4, 128),
            "max_grad_norm":        (0.1, 5.0),
            "warmup_ratio":         (0.0, 0.2),
        },
        "llm_pretrain": {
            "learning_rate":        (1e-5, 3e-4),
            "num_steps":            (1, 100000),
            "batch_size":           (16, 1024),
            "max_seq_len":          (512, 32768),
        },
        "vision_train": {
            "learning_rate":        (1e-5, 1e-2),
            "num_epochs":           (1, 100),
            "batch_size":           (4, 512),
            "weight_decay":         (0.0, 0.1),
        },
    }

    ALLOWED_ARCHITECTURES = {
        "llm_sft": {
            "Qwen2.5-7B", "Qwen2.5-14B", "Qwen2.5-72B",
            "Llama-3.1-8B", "Llama-3.1-70B",
            "InternLM2.5-7B", "InternLM2.5-20B",
            "Baichuan2-7B", "Baichuan2-13B",
            "DeepSeek-R1-7B",
        },
        "vision_train": {
            "ResNet50", "ResNet101", "ViT-B/16", "ViT-L/16",
            "EfficientNet-B4", "ConvNeXt-Base",
            "SwinTransformer-Base",
        },
        "multimodal_train": {
            "InternVL2-8B", "InternVL2-26B",
            "Qwen2-VL-7B", "Qwen2-VL-72B",
            "MiniCPM-V-2",
        },
    }

    def validate(self, config: dict, sandbox_mode: str) -> ValidationResult:
        errors = []

        # 1. 架构白名单
        arch = config.get("base_model") or config.get("architecture")
        allowed = self.ALLOWED_ARCHITECTURES.get(sandbox_mode, set())
        if arch and arch not in allowed:
            errors.append(f"Architecture '{arch}' not in whitelist for {sandbox_mode}")

        # 2. 超参数范围校验
        constraints = self.PARAM_CONSTRAINTS.get(sandbox_mode, {})
        for param, (lo, hi) in constraints.items():
            val = config.get(param)
            if val is not None and not (lo <= val <= hi):
                errors.append(
                    f"Parameter '{param}'={val} out of allowed range [{lo}, {hi}]"
                )

        # 3. 特殊规则：禁止过拟合诱导配置
        if config.get("num_epochs", 0) > 5 and config.get("learning_rate", 0) > 5e-4:
            errors.append("High LR + Many epochs combination risks memorization")

        if config.get("lora_r", 0) > 64 and config.get("lora_alpha", 0) > 128:
            errors.append("High LoRA rank with high alpha may cause overfitting")

        # 4. 禁止自定义损失函数（防后门）
        if "custom_loss_fn" in config:
            errors.append("Custom loss functions are not allowed")

        return ValidationResult(valid=len(errors) == 0, errors=errors)
```

---

## 7. 训练任务审计事件序列

```python
class TrainingAuditEmitter:
    """训练过程中的结构化审计事件发射器"""

    def on_training_start(self, session: SandboxSession, config: dict):
        audit_service.emit(AuditEvent(
            event_type=AuditEventType.TASK_STARTED,
            session_id=session.session_id,
            extra={
                "task_type": session.sandbox_mode,
                "base_model": config.get("base_model"),
                "training_strategy": config.get("strategy", "full"),
                "dp_enabled": config.get("dp_config") is not None,
                "dp_epsilon_target": config.get("dp_config", {}).get("target_epsilon"),
                "distributed_world_size": config.get("world_size", 1),
                "gpu_count": config.get("gpu_count"),
            }
        ))

    def on_epoch_end(self, epoch: int, metrics: EpochMetrics, session_id: str):
        audit_service.emit(AuditEvent(
            event_type=AuditEventType.TRAINING_EPOCH,
            session_id=session_id,
            extra={
                "epoch": epoch,
                "loss": round(metrics.loss, 4),
                "samples_seen": metrics.samples_seen,
                "epsilon_consumed": round(metrics.epsilon_consumed or 0, 4),
                "epsilon_remaining": round(metrics.epsilon_remaining or 0, 4),
                "gpu_utilization_pct": metrics.gpu_util,
                "throughput_samples_per_sec": metrics.throughput,
            }
        ))

    def on_memorization_check(
        self, report: MemorizationReport, session_id: str
    ):
        audit_service.emit(AuditEvent(
            event_type=AuditEventType.MEMORIZATION_CHECK,
            session_id=session_id,
            severity="INFO" if report.passed else "CRITICAL",
            mia_score=report.mia_advantage,
            memorization_score=report.text_extraction_rate,
            extra={
                "passed": report.passed,
                "mia_advantage": report.mia_advantage,
                "text_extraction_rate": report.text_extraction_rate,
                "image_reconstruction_score": report.image_reconstruction_score,
                "verbatim_k50_rate": report.verbatim_memorization_k50,
            }
        ))

    def on_model_export(
        self, model_path: str, watermark_payload: str, session_id: str
    ):
        audit_service.emit(AuditEvent(
            event_type=AuditEventType.MODEL_EXPORTED,
            session_id=session_id,
            extra={
                "model_path_hash": sm3_hash(model_path.encode()).hex()[:16],
                "watermark_injected": True,
                "watermark_payload_hash": sm3_hash(watermark_payload.encode()).hex(),
                "export_format": "safetensors",
            }
        ))
```

---

## 8. 配置参数

```yaml
ai_training_pipeline:
  gpu_tee:
    required_for_modes: [llm_sft, llm_pretrain, vision_train, multimodal_train]
    nvidia_cc_mode: true
    attestation_verify: true
    channel_encryption: "AES-256-GCM"   # CPU-TEE ↔ GPU-TEE 通道加密

  distributed:
    nccl_version: "2.19"
    max_nodes: 8                         # 单训练任务最大节点数
    max_gpus_per_node: 8
    ddp_bucket_size_mb: 25
    fsdp_cpu_offload: false             # 禁止 offload（防内存侧信道）

  dp_sgd:
    framework: "opacus"
    default_max_grad_norm: 1.0
    default_noise_multiplier: 1.1
    accounting_method: "rdp"            # rdp | gdp | prv

  memorization:
    run_after_training: true
    mia_samples: 200
    text_extraction_samples: 200
    k_values: [10, 20, 50, 100]        # 测试不同前缀长度的记忆率
    block_export_if_failed: true        # 记忆检测失败时阻止模型导出

  checkpoint:
    encryption: "SM4-GCM"
    save_every_n_epochs: 1
    keep_last_n: 2
    storage: "/sandbox/checkpoints"     # 沙箱内加密卷

  model_registry:
    internal_registry_url: "https://model-registry.cds.internal"
    allow_external_download: false      # 禁止从 HuggingFace 等外网下载
    allowed_model_formats: [safetensors, gguf, onnx]
    watermark_method: "weight_shift"
    watermark_max_perturbation: 1e-5
```

---

*文档：SS-07 | 版本：v1.0 | 行数：~530*
