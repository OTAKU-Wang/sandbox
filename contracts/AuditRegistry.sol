// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.0;

/// @title AuditRegistry
/// @notice On-chain anchor for CDS audit Merkle roots.
///         Each anchor records the Merkle root, a timestamp, and arbitrary metadata.
contract AuditRegistry {
    // ── State ────────────────────────────────────────────────────

    /// @notice merkle_root (bytes32) -> block timestamp of anchoring
    mapping(bytes32 => uint256) public audits;

    // ── Events ───────────────────────────────────────────────────

    /// @notice Emitted when a new audit Merkle root is anchored.
    event AuditAnchored(
        bytes32 indexed merkle_root,
        uint256 timestamp,
        string metadata
    );

    // ── External functions ───────────────────────────────────────

    /// @notice Anchor a Merkle root on-chain.
    /// @param merkleRoot  The SM3-based Merkle tree root to store.
    /// @param metadata    Human-readable metadata (e.g. batch ID, timestamp source).
    function anchorAudit(
        bytes32 merkleRoot,
        string memory metadata
    ) external {
        require(audits[merkleRoot] == 0, "AuditRegistry: already anchored");

        audits[merkleRoot] = block.timestamp;
        emit AuditAnchored(merkleRoot, block.timestamp, metadata);
    }

    /// @notice Check whether a Merkle root has been anchored.
    /// @param merkleRoot  The Merkle root to verify.
    /// @return True if the root exists on-chain, false otherwise.
    function verifyAudit(bytes32 merkleRoot) external view returns (bool) {
        return audits[merkleRoot] != 0;
    }
}
