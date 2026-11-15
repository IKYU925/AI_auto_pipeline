"""고객사(tenant). MSP처럼 한 운영 조직이 여러 고객사 환경을 맡는다(plan.md 9-7)."""
COMMON = "common"
PREFIX = "tenant-"


def tenant_of(namespace: str | None) -> str:
    """고객사는 namespace 이름(tenant-*)으로 정한다. 그 밖(shop, 노드 장애)은 운영 조직 공통이다."""
    return namespace if namespace and namespace.startswith(PREFIX) else COMMON
