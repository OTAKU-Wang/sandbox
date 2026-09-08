"""W17: Helm chart hardening — structure, safety knobs, YAML validity.

helm binary is not available in this environment, so validation is:
1. values.yaml / Chart.yaml parse as strict YAML,
2. required availability/safety manifests exist with the right markers,
3. Go-template conditionals are balanced ({{- if }} has {{- end }}).
Real cluster rendering remains a W17 environment-acceptance item.
"""
from pathlib import Path

import pytest
import yaml

CHART = Path("helm/cds")


def test_values_and_chart_yaml_valid():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert values["replicaCount"] >= 1
    assert chart["apiVersion"] == "v2"
    assert "autoscaling" in values
    assert values["autoscaling"]["maxReplicas"] >= values["autoscaling"]["minReplicas"] >= 1
    assert values["podDisruptionBudget"]["minAvailable"] >= 1


def test_env_block_has_w13_w15_w19_keys():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    env = values["api"]["env"]
    assert env["CDS_EXEC_STREAM_MAX_CONCURRENT"] == "3"
    assert env["CDS_EGRESS_AUDIT_ENABLED"] == "true"
    assert env["CDS_EGRESS_AUDIT_ENCRYPTION_ENABLED"] == "true"
    assert env["CDS_NODE_STALE_SECONDS"] == "300"
    assert env["CDS_SHARED_VOLUME_ROOT"] == "/data/shared-volumes"


def test_availability_manifests_present():
    hpa = (CHART / "templates" / "hpa.yaml").read_text()
    pdb = (CHART / "templates" / "pdb.yaml").read_text()
    cm = (CHART / "templates" / "configmap.yaml").read_text()
    assert "HorizontalPodAutoscaler" in hpa and "autoscaling/v2" in hpa
    assert "PodDisruptionBudget" in pdb and "minAvailable" in pdb
    assert "kind: ConfigMap" in cm and ".Values.api.env" in cm


def test_api_deployment_hardening():
    dep = (CHART / "templates" / "deployment-api.yaml").read_text()
    assert "envFrom:" in dep and "configMapRef" in dep
    assert "runAsNonRoot: true" in dep
    assert "allowPrivilegeEscalation: false" in dep
    assert "topologySpreadConstraints" in dep
    assert "livenessProbe" in dep and "readinessProbe" in dep



def test_template_conditionals_balanced():
    for template in (CHART / "templates").glob("*.yaml"):
        text = template.read_text()
        opens = text.count("{{- if") + text.count("{{- range") + text.count("{{- with")
        closes = text.count("{{- end")
        assert opens == closes, template.name


# Components whose `enabled: true` is satisfied by a first-class chart
# template (component name → template file). Anything else must be a declared
# Chart.yaml dependency or appear in values.externalComponents.
COMPONENT_TEMPLATES = {
    "autoscaling": "hpa.yaml",
    "ingress": "ingress.yaml",
    "api": "deployment-api.yaml",
    "frontend": "deployment-frontend.yaml",
}


def test_enabled_components_have_templates_or_whitelist():
    """Every `*.enabled: true` component must be templated in-chart, a declared
    Chart.yaml dependency, or listed in values.externalComponents — so a
    dangling `enabled` flag (a component the chart claims but does not ship)
    cannot silently reappear after the N4 topology alignment."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())

    external = set(values.get("externalComponents", []))
    dependencies = {d["name"] for d in chart.get("dependencies", [])}
    existing_templates = {t.name for t in (CHART / "templates").glob("*.yaml")}

    enabled_components = {
        name for name, cfg in values.items()
        if isinstance(cfg, dict) and cfg.get("enabled") is True
    }
    assert enabled_components, "no enabled components found — topology gate is vacuous"

    for comp in sorted(enabled_components):
        template = COMPONENT_TEMPLATES.get(comp)
        satisfied = (
            comp in external
            or comp in dependencies
            or (template is not None and template in existing_templates)
        )
        assert satisfied, (
            f"component {comp!r} is enabled but has no in-chart template, no "
            f"Chart.yaml dependency, and is not in values.externalComponents"
        )
