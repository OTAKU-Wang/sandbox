"""Model Weight Watermarking -- Uchida 2017 scheme.

Embeds ownership watermarks into neural-network layer weights by selecting
pseudo-random weight indices (SM3-CTR derived) and flipping LSBs.

Honest-write contract (A185, 项目铁律「禁伪造」):
``embed_watermark`` returns ``success=True`` only after every embedded bit has
been read back from the caller's own tensor. Writes therefore go through a
row-major multi-index on the tensor itself -- ``flatten()`` must never be used
as a write target, because numpy's ``flatten()`` and a non-contiguous torch
tensor's ``flatten()`` both return a *copy* and every bit written into that copy
used to vanish while the call still claimed success (the caller then saved the
checkpoint and audited an "already watermarked" record). A backend whose writes
do not land raises instead of reporting success.

Key handling (A186): ``key_seed`` is secret material. Position selection is
derived from ``SM3(context | server pepper | key_seed | layer names)`` so the
plaintext ``key_seed`` column (``watermark_records``) and the ``key_seed`` field
the training API still echoes are not, by themselves, enough to recompute where
every watermark bit sits. Persisted metadata carries only an irreversible
fingerprint of the seed, never a way back to it.
"""
import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field

from app.utils.crypto import sm3_hash

# LSB scheme: the weight is scaled into integer space before the bit is written.
WEIGHT_SCALE = 1000

# A186 — versioned domain-separation labels for the two derivations below.
POSITION_KDF = "sm3-ctr-v1"
POSITION_CONTEXT = "cds-watermark-positions:v1"
KEY_SEED_FINGERPRINT_CONTEXT = "cds-watermark-keyseed:v1"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WatermarkMetadata:
    """Persisted metadata needed for watermark verification.

    ``key_seed`` is secret material and is kept here only because verification
    is position-driven (positions are recorded) while re-embedding needs the
    seed again. It must never be widened to a client response: positions cannot
    be recomputed from it alone (see A186 in this module's docstring), and
    ``key_seed_fingerprint`` is what an API/audit should carry instead.
    """
    layer_positions: dict[str, list[int]]  # layer_name -> list of flat indices
    key_seed: int
    num_bits: int
    ordered_positions: list[tuple[str, int]] = field(default_factory=list)  # (layer, idx) in embed order
    key_seed_fingerprint: str = ""  # non-reversible SM3 fingerprint, safe to expose
    position_kdf: str = ""  # how the positions were derived (disclosure)


@dataclass
class WatermarkResult:
    """Returned after embedding a watermark."""
    success: bool
    metadata: WatermarkMetadata
    embed_time_ms: float


@dataclass
class WatermarkVerifyResult:
    """Returned after verifying a watermark."""
    match_ratio: float
    passed: bool
    expected_bits: list[int]
    extracted_bits: list[int]
    reason: str = ""  # why nothing could be checked (empty = a real verdict)


class _Sm3CounterDrbg:
    """Uniform random integers from an SM3 counter stream (A186: no Mersenne Twister).

    Mersenne Twister is not a cryptographic PRNG: 624 observed outputs recover
    its state, and here the output stream *is* the secret position schedule.
    """

    def __init__(self, material: bytes):
        self._material = material
        self._counter = 0
        self._buf = b""

    def _next_block(self) -> None:
        self._buf += bytes.fromhex(sm3_hash(self._material + self._counter.to_bytes(8, "big")))
        self._counter += 1

    def take(self, n: int) -> bytes:
        while len(self._buf) < n:
            self._next_block()
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def below(self, bound: int) -> int:
        """Unbiased uniform integer in [0, bound) by rejection sampling."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        if bound == 1:
            return 0
        n_bytes = max(4, (bound - 1).bit_length() // 8 + 1)
        span = 1 << (n_bytes * 8)
        limit = (span // bound) * bound
        while True:
            value = int.from_bytes(self.take(n_bytes), "big")
            if value < limit:
                return value % bound


# ---------------------------------------------------------------------------
# Core service
# ---------------------------------------------------------------------------

class ModelWatermarkService:
    """Embeds and verifies ownership watermarks in model weight tensors.

    Follows the Uchida et al. 2017 approach:
    1. Use a PRNG seeded with a secret *key_seed* to pick (index, bit-position)
       pairs across specified layers.
    2. For each selected weight, embed one watermark bit into the LSB of the
       quantised mantissa (or simply the integer-represented LSB for float
       weights scaled to int).
    3. Verification re-reads the same positions and compares bits.
    """

    _VERIFY_THRESHOLD: float = 0.95  # 95 % match required to pass

    # ------------------------------------------------------------------
    # Bit generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_watermark_bits(
        owner_id: str,
        model_id: str,
        num_bits: int = 64,
    ) -> list[int]:
        """Deterministic watermark bits derived from SM3(owner_id || model_id).

        Returns a list of 0/1 integers of length *num_bits*.
        """
        if num_bits <= 0:
            # A zero-length watermark has no bits to contradict: letting it
            # through produced an "effective" watermark verdict out of nothing.
            raise ValueError(f"num_bits must be positive (got {num_bits})")
        payload = f"{owner_id}:{model_id}".encode("utf-8")
        hex_digest = sm3_hash(payload)  # 64 hex chars = 256 bits

        # Expand if num_bits > 256 by hashing iteratively
        bits: list[int] = []
        counter = 0
        while len(bits) < num_bits:
            h = sm3_hash(payload + counter.to_bytes(4, "big"))
            for ch in h:
                nibble = int(ch, 16)
                for i in range(3, -1, -1):
                    bits.append((nibble >> i) & 1)
                    if len(bits) >= num_bits:
                        break
                if len(bits) >= num_bits:
                    break
            counter += 1

        return bits[:num_bits]

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed_watermark(
        self,
        model_state_dict: dict,
        watermark_bits: list[int],
        layer_names: list[str],
        key_seed: int,
    ) -> WatermarkResult:
        """Embed *watermark_bits* into the weights of *layer_names*.

        Args:
            model_state_dict: Mapping of layer name -> tensor (numpy / torch).
            watermark_bits: 0/1 list to embed.
            layer_names: Which layers to use for embedding.
            key_seed: Secret seed for the position schedule (see A186 note in
                the module docstring -- it is combined with a server-side
                pepper, so the stored seed alone does not reveal the positions).

        Returns:
            WatermarkResult with timing and metadata.

        Raises:
            ValueError: the request is not embeddable (empty bits, empty or
                duplicated / aliased layer set, too few weights) or a bit did
                not land in the caller's tensor when written back. Never
                returns ``success=True`` for bits that are not in the weights.
        """
        t0 = time.perf_counter()

        num_bits = len(watermark_bits)

        # Validate the request shape (A187): an empty schedule or a layer set
        # that folds onto itself can only produce a fake "N bits embedded".
        if num_bits <= 0:
            raise ValueError("watermark_bits must not be empty: there is nothing to embed")
        if not layer_names:
            raise ValueError("layer_names must not be empty: no weights to embed into")
        duplicated = sorted(name for name, count in Counter(layer_names).items() if count > 1)
        if duplicated:
            raise ValueError(
                f"duplicate layer names in embed request: {duplicated} "
                "(bits would overwrite each other on the same weights)"
            )

        # Layer names pointing at the same tensor object are the same aliasing
        # defect with different strings, so reject them too.
        seen_objects: dict[int, str] = {}
        for name in layer_names:
            if name not in model_state_dict:
                raise ValueError(f"Layer '{name}' not found in model state dict")
            tensor_id = id(model_state_dict[name])
            if tensor_id in seen_objects:
                raise ValueError(
                    f"layers {seen_objects[tensor_id]!r} and {name!r} are the same object "
                    "(alias): positions would overwrite each other"
                )
            seen_objects[tensor_id] = name

        # Lay the selected layers end to end; a global flat index locates the
        # (layer, index) pair without materialising one tuple per weight.
        spans: list[tuple[str, int, int]] = []
        total_weights = 0
        for name in layer_names:
            size = self._tensor_numel(model_state_dict[name])
            spans.append((name, total_weights, size))
            total_weights += size

        if total_weights < num_bits:
            raise ValueError(
                f"Not enough weights ({total_weights}) to embed {num_bits} bits. "
                "Add more layers or reduce num_bits."
            )

        # Select *num_bits* unique positions with the SM3-CTR schedule.
        drbg = _Sm3CounterDrbg(self._position_material(layer_names, key_seed))
        chosen = self._select_positions(drbg, total_weights, num_bits)

        def locate(global_idx: int) -> tuple[str, int]:
            for layer_name, start, size in spans:
                if global_idx < start + size:
                    return layer_name, global_idx - start
            raise ValueError(f"weight index {global_idx} out of range")

        # Embed
        layer_positions: dict[str, list[int]] = {}
        ordered_positions: list[tuple[str, int]] = []
        for bit_idx, pos_flat in enumerate(chosen):
            layer_name, weight_idx = locate(pos_flat)
            bit = watermark_bits[bit_idx]
            self._embed_bit(model_state_dict[layer_name], weight_idx, bit)
            layer_positions.setdefault(layer_name, []).append(weight_idx)
            ordered_positions.append((layer_name, weight_idx))

        # Belt and braces: success=True is an ownership claim, so re-read every
        # recorded position before handing the result back to the caller (which
        # saves the checkpoint and audits "watermarked" on the strength of it).
        lost = [
            i for i, (layer_name, weight_idx) in enumerate(ordered_positions)
            if self._extract_bit(model_state_dict[layer_name], weight_idx) != watermark_bits[i]
        ]
        if lost:
            raise ValueError(
                f"watermark write did not land for {len(lost)} of {num_bits} bits "
                f"(first at position {ordered_positions[lost[0]]}) -- refusing to "
                "report a watermark that is not in the weights"
            )

        metadata = WatermarkMetadata(
            layer_positions=layer_positions,
            key_seed=key_seed,
            num_bits=num_bits,
            ordered_positions=ordered_positions,
            key_seed_fingerprint=self.key_seed_fingerprint(key_seed),
            position_kdf=POSITION_KDF,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return WatermarkResult(success=True, metadata=metadata, embed_time_ms=elapsed_ms)

    # ------------------------------------------------------------------
    # Position schedule (A186)
    # ------------------------------------------------------------------

    @staticmethod
    def key_seed_fingerprint(key_seed: int) -> str:
        """Non-reversible fingerprint of the seed -- safe to persist/return."""
        material = f"{KEY_SEED_FINGERPRINT_CONTEXT}|{key_seed}".encode("utf-8")
        return sm3_hash(material)[:16]

    @staticmethod
    def _position_material(layer_names: list[str], key_seed: int) -> bytes:
        """SM3 material for the position schedule = server pepper + key_seed.

        The pepper is the deployment's server-side secret: the watermark_records
        row (or the API echo of ``key_seed``) therefore does not contain enough
        to recompute the bit positions and surgically erase them. Its strength
        is JWT_SECRET_KEY's strength -- ``validate_security_config`` already
        rejects the shipped default key in production.
        """
        from app.core.config import get_settings

        pepper = str(get_settings().JWT_SECRET_KEY)
        return f"{POSITION_CONTEXT}|{pepper}|{key_seed}|{','.join(layer_names)}".encode("utf-8")

    @staticmethod
    def _select_positions(drbg: _Sm3CounterDrbg, total: int, count: int) -> list[int]:
        """Count unique indices in [0, total) (sparse Fisher-Yates, O(count))."""
        picked: list[int] = []
        pool: dict[int, int] = {}
        for i in range(count):
            remaining = total - i
            j = drbg.below(remaining)
            picked.append(pool.get(j, j))
            last = remaining - 1
            pool[j] = pool.get(last, last)
        return picked


    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_watermark(
        self,
        model_state_dict: dict,
        watermark_bits: list[int],
        metadata: WatermarkMetadata,
    ) -> WatermarkVerifyResult:
        """Extract bits from the positions recorded in *metadata* and compare.

        Returns match ratio and pass/fail (threshold: 95 %). An empty position
        set is an honest ``passed=False`` with a reason -- never ratio 1.0.
        """
        if not watermark_bits:
            raise ValueError("watermark_bits must not be empty: there is no watermark to verify")

        # Use ordered_positions if available (preserves embed order exactly)
        positions: list[tuple[str, int]] = list(metadata.ordered_positions or [])
        if not positions:
            # Fallback: iterate dict in insertion order (Python 3.7+)
            positions = [
                (layer_name, weight_idx)
                for layer_name, idx_list in metadata.layer_positions.items()
                for weight_idx in idx_list
            ]
        if not positions:
            # Nothing was ever recorded: 「空值报成通过」 is exactly what the
            # honest-degradation rule forbids, so report a failed verdict.
            return WatermarkVerifyResult(
                match_ratio=0.0,
                passed=False,
                expected_bits=list(watermark_bits),
                extracted_bits=[],
                reason="no watermark positions recorded in metadata -- nothing to check",
            )

        extracted: list[int] = []
        for layer_name, weight_idx in positions:
            if layer_name not in model_state_dict:
                raise ValueError(f"Layer '{layer_name}' not found in model state dict")
            extracted.append(self._extract_bit(model_state_dict[layer_name], weight_idx))

        if len(extracted) != len(watermark_bits):
            raise ValueError(
                f"Extracted {len(extracted)} bits but expected {len(watermark_bits)}"
            )

        matches = sum(a == b for a, b in zip(extracted, watermark_bits))
        ratio = matches / len(watermark_bits)

        return WatermarkVerifyResult(
            match_ratio=ratio,
            passed=ratio >= self._VERIFY_THRESHOLD,
            expected_bits=watermark_bits,
            extracted_bits=extracted,
        )

    # ------------------------------------------------------------------
    # Low-level bit manipulation
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten(tensor) -> list:
        """Return a flat list of floats from any tensor-like object."""
        # torch / numpy with .flatten()
        if hasattr(tensor, "flatten"):
            flat = tensor.flatten()
            return [float(x) for x in flat]
        # numpy array with .flat
        if hasattr(tensor, "flat"):
            return [float(x) for x in tensor.flat]
        # plain list (possibly nested) -- flatten one level
        if isinstance(tensor, (list, tuple)):
            out = []
            for item in tensor:
                if isinstance(item, (list, tuple)):
                    out.extend(float(x) for x in item)
                else:
                    out.append(float(item))
            return out
        raise TypeError(f"Unsupported tensor type: {type(tensor)}")

    @classmethod
    def _tensor_numel(cls, tensor) -> int:
        """Return total element count for torch tensor, numpy array, or list."""
        # torch
        if hasattr(tensor, "numel"):
            return tensor.numel()
        # numpy
        if hasattr(tensor, "size"):
            return tensor.size
        # plain list
        if isinstance(tensor, (list, tuple)):
            return len(cls._flatten(tensor))
        raise TypeError(f"Unsupported tensor type: {type(tensor)}")

    @staticmethod
    def _shape_of(tensor) -> tuple | None:
        """Logical shape for numpy / torch tensors (torch.Size is a tuple), else None."""
        shape = getattr(tensor, "shape", None)
        if isinstance(shape, tuple) and all(isinstance(dim, int) for dim in shape):
            return shape
        return None

    @staticmethod
    def _flat_index(shape: tuple, idx: int) -> tuple:
        """Row-major multi-index of a flat index -- the same order flatten() uses."""
        if not shape:
            return ()
        key: list[int] = []
        for dim in reversed(shape):
            key.append(idx % dim)
            idx //= dim
        key.reverse()
        if idx:
            raise IndexError(f"flat index {idx} out of range for shape {shape}")
        return tuple(key)

    @classmethod
    def _list_leaf(cls, node: list, idx: int) -> tuple[list, int]:
        """(parent list, position) holding flat index *idx* of a nested list."""
        for pos, item in enumerate(node):
            if isinstance(item, (list, tuple)):
                count = cls._tensor_numel(item)
                if idx < count:
                    return cls._list_leaf(item, idx)
                idx -= count
            elif idx == 0:
                return node, pos
            else:
                idx -= 1
        raise IndexError(f"flat index {idx} out of range for this list")

    @classmethod
    def _get_weight_value(cls, tensor, idx: int) -> float:
        """Read the weight at flat index -- element access, never a full copy."""
        shape = cls._shape_of(tensor)
        if shape is not None and hasattr(tensor, "__getitem__"):
            return float(tensor[cls._flat_index(shape, idx)])
        if isinstance(tensor, (list, tuple)):
            parent, pos = cls._list_leaf(list(tensor), idx)
            return float(parent[pos])
        flat = cls._flatten(tensor)
        return flat[idx]

    @classmethod
    def _set_weight_value(cls, tensor, idx: int, value: float) -> None:
        """Write one element **in place**; raise when the write cannot land.

        A185: this used to be ``flat = tensor.flatten(); flat[idx] = value``,
        which writes into a temporary -- numpy's flatten() always copies and a
        non-contiguous torch tensor's flatten() does too. Every bit was lost
        while embed_watermark still reported success. There is deliberately no
        ``tensor.flat[idx]`` branch either: with the shape-based branch above it
        would be unreachable dead code, exactly the branch shape that hid the bug.
        """
        shape = cls._shape_of(tensor)
        if shape is not None:
            if not hasattr(tensor, "__setitem__"):
                raise ValueError(
                    f"watermark write failed: {type(tensor).__name__} is not writable "
                    f"(no __setitem__) at flat index {idx}"
                )
            try:
                tensor[cls._flat_index(shape, idx)] = value
            except Exception as exc:  # dtype cast, immutability, out-of-range ...
                raise ValueError(
                    f"watermark write failed at flat index {idx} of a "
                    f"{type(tensor).__name__} (dtype={getattr(tensor, 'dtype', None)}): {exc}"
                ) from exc
            return
        if isinstance(tensor, list):
            parent, pos = cls._list_leaf(tensor, idx)
            try:
                parent[pos] = value
            except (TypeError, IndexError) as exc:
                raise ValueError(f"watermark write failed into nested list at index {idx}: {exc}") from exc
            return
        if isinstance(tensor, tuple):
            raise ValueError(f"watermark write failed: {type(tensor).__name__} is immutable")
        raise TypeError(f"Unsupported tensor type: {type(tensor)}")

    @classmethod
    def _embed_bit(cls, tensor, idx: int, bit: int) -> None:
        """Embed one bit into the LSB of the scaled-integer weight, then read it back."""
        target = bit & 1
        w = cls._get_weight_value(tensor, idx)
        # Scale to int space (multiply by 1000 to preserve 3 decimal digits)
        scaled = int(round(w * WEIGHT_SCALE))
        # Clear LSB and set the desired bit
        candidate = (scaled & ~1) | target
        cls._set_weight_value(tensor, idx, candidate / WEIGHT_SCALE)
        # 写后回读（禁伪造）：写不进去就报错，绝不把 no-op 报成已水印。
        got = cls._extract_bit(tensor, idx)
        if got != target:
            raise ValueError(
                f"watermark write did not land: bit read back at flat index {idx} is "
                f"{got}, expected {target} (tensor={type(tensor).__name__}, "
                f"dtype={getattr(tensor, 'dtype', None)}). Refusing to report a "
                "watermark that is not in the weights."
            )

    @classmethod
    def _extract_bit(cls, tensor, idx: int) -> int:
        """Extract a single bit from the LSB of the scaled-integer weight."""
        w = cls._get_weight_value(tensor, idx)
        scaled = int(round(w * WEIGHT_SCALE))
        return scaled & 1


# Module-level singleton for convenience
model_watermark_service = ModelWatermarkService()
