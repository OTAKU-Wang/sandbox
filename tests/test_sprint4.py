"""Sprint 4 tests — version management + certificate chain verification."""
import pytest
from app.models.data_product import DataProduct, DataProductStatus


def test_data_product_version_fields():
    """DataProduct model has version management fields."""
    product = DataProduct(
        name="test-product",
        product_type="structured",
        version=1,
        is_latest=True,
        change_summary="Initial version",
    )
    assert product.version == 1
    assert product.is_latest is True
    assert product.change_summary == "Initial version"
    assert product.parent_id is None


def test_data_product_version_chain():
    """DataProduct version chain links correctly."""
    parent_id = "00000000-0000-0000-0000-000000000001"
    product = DataProduct(
        name="test-product-v2",
        product_type="structured",
        version=2,
        parent_id=parent_id,
        is_latest=True,
        change_summary="Updated schema",
    )
    assert product.version == 2
    assert str(product.parent_id) == parent_id
    assert product.is_latest is True


def test_data_product_status_enum():
    """DataProductStatus covers expected states."""
    assert DataProductStatus.DRAFT.value == "draft"
    assert DataProductStatus.PUBLISHED.value == "published"
    assert DataProductStatus.ARCHIVED.value == "archived"


def test_certificate_service_has_verify_chain():
    """CertificateService has verify_chain method."""
    from app.services.certificate_service import certificate_service
    assert hasattr(certificate_service, "verify_chain")
    assert callable(certificate_service.verify_chain)


def test_certificate_service_has_build_cert_pem():
    """CertificateService has _build_cert_pem method."""
    from app.services.certificate_service import certificate_service
    assert hasattr(certificate_service, "_build_cert_pem")
