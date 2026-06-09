"""Model Weight Watermarking -- Uchida 2017 scheme.

Embeds ownership watermarks into neural-network layer weights by selecting
pseudo-random weight indices (seeded PRNG) and flipping LSBs.
"""
import hashlib
import random
import time
from dataclasses import dataclass, field

from app.utils.crypto import sm3_hash


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WatermarkMetadata:
    """Persisted metadata needed for watermark verification."""
    layer_positions: dict[str, list[int]]  # layer_name -> list of flat indices
    key_seed: int
    num_bits: int
    ordered_positions: list[tuple[str, int]] = field(default_factory=list)  # (layer, idx) in embed order


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
            key_seed: Secret seed for the PRNG that selects weight positions.

        Returns:
            WatermarkResult with timing and metadata.
        """
        t0 = time.perf_counter()

        num_bits = len(watermark_bits)
        rng = random.Random(key_seed)

        # Validate layers exist
        for name in layer_names:
            if name not in model_state_dict:
                raise ValueError(f"Layer '{name}' not found in model state dict")

        # Build a flat list of (layer_name, flat_index) for all eligible weights
        all_positions: list[tuple[str, int]] = []
        layer_sizes: dict[str, int] = {}
        for name in layer_names:
            tensor = model_state_dict[name]
            # Accept torch tensors, numpy arrays, or nested lists
            size = self._tensor_numel(tensor)
            layer_sizes[name] = size
            for idx in range(size):
                all_positions.append((name, idx))

        if len(all_positions) < num_bits:
            raise ValueError(
                f"Not enough weights ({len(all_positions)}) to embed {num_bits} bits. "
                "Add more layers or reduce num_bits."
            )

        # Use PRNG to select *num_bits* unique positions
        chosen = rng.sample(range(len(all_positions)), num_bits)

        # Embed
        layer_positions: dict[str, list[int]] = {}
        ordered_positions: list[tuple[str, int]] = []
        for bit_idx, pos_flat in enumerate(chosen):
            layer_name, weight_idx = all_positions[pos_flat]
            bit = watermark_bits[bit_idx]
            self._embed_bit(model_state_dict[layer_name], weight_idx, bit)
            layer_positions.setdefault(layer_name, []).append(weight_idx)
            ordered_positions.append((layer_name, weight_idx))

        metadata = WatermarkMetadata(
            layer_positions=layer_positions,
            key_seed=key_seed,
            num_bits=num_bits,
            ordered_positions=ordered_positions,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return WatermarkResult(success=True, metadata=metadata, embed_time_ms=elapsed_ms)

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

        Returns match ratio and pass/fail (threshold: 95 %).
        """
        extracted: list[int] = []

        # Use ordered_positions if available (preserves embed order exactly)
        if metadata.ordered_positions:
            for layer_name, weight_idx in metadata.ordered_positions:
                if layer_name not in model_state_dict:
                    raise ValueError(f"Layer '{layer_name}' not found in model state dict")
                bit = self._extract_bit(model_state_dict[layer_name], weight_idx)
                extracted.append(bit)
        else:
            # Fallback: iterate dict in insertion order (Python 3.7+)
            for layer_name, positions in metadata.layer_positions.items():
                if layer_name not in model_state_dict:
                    raise ValueError(f"Layer '{layer_name}' not found in model state dict")
                tensor = model_state_dict[layer_name]
                for weight_idx in positions:
                    bit = self._extract_bit(tensor, weight_idx)
                    extracted.append(bit)

        if len(extracted) != len(watermark_bits):
            raise ValueError(
                f"Extracted {len(extracted)} bits but expected {len(watermark_bits)}"
            )

        matches = sum(a == b for a, b in zip(extracted, watermark_bits))
        ratio = matches / len(watermark_bits) if watermark_bits else 1.0

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

    @classmethod
    def _get_weight_value(cls, tensor, idx: int) -> float:
        """Read weight at flat index."""
        # Fast path for flat lists
        if isinstance(tensor, list) and tensor and not isinstance(tensor[0], (list, tuple)):
            return float(tensor[idx])
        flat = cls._flatten(tensor)
        return flat[idx]

    @classmethod
    def _set_weight_value(cls, tensor, idx: int, value: float) -> None:
        """Write weight at flat index (in-place for torch/numpy; list replacement)."""
        # torch tensor
        if hasattr(tensor, "flatten") and hasattr(tensor, "__setitem__"):
            flat = tensor.flatten()
            flat[idx] = value
            return
        # numpy array
        if hasattr(tensor, "flat"):
            tensor.flat[idx] = value
            return
        # plain flat list (all elements are scalars)
        if isinstance(tensor, list) and tensor and not isinstance(tensor[0], (list, tuple)):
            tensor[idx] = value
            return
        # nested list -- find the right inner element
        if isinstance(tensor, list):
            offset = 0
            for inner in tensor:
                if isinstance(inner, (list, tuple)):
                    if offset + len(inner) > idx:
                        inner[idx - offset] = value
                        return
                    offset += len(inner)
                else:
                    if offset == idx:
                        tensor[idx] = value  # type: ignore[index]
                        return
                    offset += 1
        raise TypeError(f"Unsupported tensor type: {type(tensor)}")

    @classmethod
    def _embed_bit(cls, tensor, idx: int, bit: int) -> None:
        """Embed a single bit into the LSB of the scaled-integer weight."""
        w = cls._get_weight_value(tensor, idx)
        # Scale to int space (multiply by 1000 to preserve 3 decimal digits)
        scaled = int(round(w * 1000))
        # Clear LSB and set the desired bit
        scaled = (scaled & ~1) | (bit & 1)
        cls._set_weight_value(tensor, idx, scaled / 1000.0)

    @classmethod
    def _extract_bit(cls, tensor, idx: int) -> int:
        """Extract a single bit from the LSB of the scaled-integer weight."""
        w = cls._get_weight_value(tensor, idx)
        scaled = int(round(w * 1000))
        return scaled & 1


# Module-level singleton for convenience
model_watermark_service = ModelWatermarkService()
