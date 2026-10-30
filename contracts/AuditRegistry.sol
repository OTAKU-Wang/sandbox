// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.0;

/// @title AuditRegistry
/// @notice On-chain anchor for CDS audit Merkle roots.
///         Each anchor records the Merkle root, a timestamp, the anchoring account
///         and arbitrary metadata.
/// @dev A195: anchoring is restricted to accounts the owner has authorized, and every
///      anchor is attributed to `msg.sender` (state + event). Without both, anyone who
///      could compute a root would win the first-writer race, plant arbitrary metadata
///      behind it, permanently DoS the platform's own `anchorAudit` for that root
///      (`require(… == 0)` never self-heals), and leave the compliance report unable to
///      tell the platform's anchor from a third party's.
contract AuditRegistry {
    // ── Types ────────────────────────────────────────────────────

    struct AuditRecord {
        /// @notice block timestamp of the anchoring transaction (0 = never anchored)
        uint256 timestamp;
        /// @notice account that anchored this root (address(0) = never anchored)
        address anchorer;
    }

    // ── State ────────────────────────────────────────────────────

    /// @notice Account that administers the anchorer allowlist.
    address public owner;

    /// @notice Accounts allowed to anchor (deployer is authorized at construction).
    mapping(address => bool) public authorizedAnchors;

    /// @notice merkle_root (bytes32) -> anchoring record (timestamp + anchorer).
    mapping(bytes32 => AuditRecord) public audits;

    // ── Events ───────────────────────────────────────────────────

    /// @notice Emitted when ownership of the registry moves.
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /// @notice Emitted when an account is added to or removed from the anchorer allowlist.
    event AnchorerAuthorizationChanged(address indexed anchorer, bool authorized);

    /// @notice Emitted when a new audit Merkle root is anchored.
    /// @param anchorer The account that performed the anchoring — verification side must
    ///        compare this against its own allowlist before trusting an anchor.
    event AuditAnchored(
        bytes32 indexed merkle_root,
        address indexed anchorer,
        uint256 timestamp,
        string metadata
    );

    // ── Modifiers ────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "AuditRegistry: caller is not the owner");
        _;
    }

    modifier onlyAnchorer() {
        require(
            msg.sender == owner || authorizedAnchors[msg.sender],
            "AuditRegistry: sender is not an authorized anchorer"
        );
        _;
    }

    // ── Constructor ──────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
        authorizedAnchors[msg.sender] = true;
        emit AnchorerAuthorizationChanged(msg.sender, true);
    }

    // ── Admin ────────────────────────────────────────────────────

    /// @notice Hand the registry to another admin account.
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "AuditRegistry: new owner is the zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
        if (!authorizedAnchors[newOwner]) {
            authorizedAnchors[newOwner] = true;
            emit AnchorerAuthorizationChanged(newOwner, true);
        }
    }

    /// @notice Add or remove an anchoring account (e.g. the platform's anchoring key).
    function setAuthorizedAnchor(address anchorer, bool authorized) external onlyOwner {
        require(anchorer != address(0), "AuditRegistry: anchorer is the zero address");
        authorizedAnchors[anchorer] = authorized;
        emit AnchorerAuthorizationChanged(anchorer, authorized);
    }

    // ── External functions ───────────────────────────────────────

    /// @notice Anchor a Merkle root on-chain. Only authorized accounts may write.
    /// @param merkleRoot  The SM3-based Merkle tree root to store.
    /// @param metadata    Human-readable metadata (e.g. batch ID, timestamp source).
    function anchorAudit(
        bytes32 merkleRoot,
        string memory metadata
    ) external onlyAnchorer {
        require(merkleRoot != bytes32(0), "AuditRegistry: empty merkle root");
        require(audits[merkleRoot].timestamp == 0, "AuditRegistry: already anchored");

        audits[merkleRoot] = AuditRecord(block.timestamp, msg.sender);
        emit AuditAnchored(merkleRoot, msg.sender, block.timestamp, metadata);
    }

    // ── View functions ───────────────────────────────────────────

    /// @notice Check whether a Merkle root has been anchored by anyone.
    /// @param merkleRoot  The Merkle root to verify.
    /// @return True if the root exists on-chain, false otherwise.
    function verifyAudit(bytes32 merkleRoot) external view returns (bool) {
        return audits[merkleRoot].timestamp != 0;
    }

    /// @notice Who anchored a Merkle root (address(0) when it is not anchored).
    ///         Lets the verification side tell "we anchored this ourselves" apart from
    ///         "someone else got there first" instead of treating any anchor as authoritative.
    /// @param merkleRoot  The Merkle root to attribute.
    /// @return The anchoring account, or address(0) if the root is unanchored.
    function anchoredBy(bytes32 merkleRoot) external view returns (address) {
        return audits[merkleRoot].anchorer;
    }

    /// @notice Check whether a specific account anchored this root.
    /// @param merkleRoot  The Merkle root to verify.
    /// @param anchorer    The account the caller trusts.
    /// @return True only when that exact account wrote this anchor.
    function verifyAuditBy(bytes32 merkleRoot, address anchorer) external view returns (bool) {
        return audits[merkleRoot].anchorer == anchorer && audits[merkleRoot].timestamp != 0;
    }
}
