import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

RAILWAY_API_URL = "https://backboard.railway.app/graphql/v2"
RAILWAY_TOKEN = os.getenv("RAILWAY_API_TOKEN", "")
GITHUB_REPO = "prostoArtemG/shop_bot"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {RAILWAY_TOKEN}",
}

async def graphql(query: str, variables: dict = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(RAILWAY_API_URL, json=payload, headers=HEADERS)
        logger.info("Railway API response: %s", resp.text[:500])
        return resp.json()

async def create_project(name: str) -> str:
    """Create new Railway project, return project ID."""
    query = """
    mutation projectCreate($input: ProjectCreateInput!) {
        projectCreate(input: $input) {
            id
            name
        }
    }
    """
    variables = {
        "input": {
            "name": name,
            "description": f"Shop bot for {name}",
        }
    }
    result = await graphql(query, variables)
    return result["data"]["projectCreate"]["id"]

async def create_service_from_github(project_id: str, repo: str, name: str) -> str:
    """Deploy service from GitHub repo, return service ID."""
    query = """
    mutation serviceCreate($input: ServiceCreateInput!) {
        serviceCreate(input: $input) {
            id
            name
        }
    }
    """
    variables = {
        "input": {
            "projectId": project_id,
            "name": name,
            "source": {
                "repo": repo,
            }
        }
    }
    result = await graphql(query, variables)
    return result["data"]["serviceCreate"]["id"]

async def create_postgres(project_id: str, environment_id: str) -> str:
    """Add PostgreSQL to project using serviceCreate with image."""
    query = """
    mutation serviceCreate($input: ServiceCreateInput!) {
        serviceCreate(input: $input) {
            id
            name
        }
    }
    """
    variables = {
        "input": {
            "projectId": project_id,
            "name": "Postgres",
            "source": {
                "image": "postgres:16"
            }
        }
    }
    result = await graphql(query, variables)
    logger.info("create_postgres result: %s", result)
    if "errors" in result:
        logger.warning("Postgres creation failed: %s", result["errors"])
        return ""
    pg_service_id = result.get("data", {}).get("serviceCreate", {}).get("id", "")

    if pg_service_id and environment_id:
        pg_vars = {
            "POSTGRES_DB": "railway",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres123",
            "PGDATA": "/var/lib/postgresql/data/pgdata",
        }
        await set_variables(project_id, pg_service_id, environment_id, pg_vars)
        logger.info("Postgres service created: %s", pg_service_id)

    return pg_service_id

async def get_environment_id(project_id: str) -> str:
    """Get the default environment ID for a project."""
    query = """
    query getEnvironment($projectId: String!) {
        environments(projectId: $projectId) {
            edges {
                node {
                    id
                    name
                }
            }
        }
    }
    """
    variables = {"projectId": project_id}
    result = await graphql(query, variables)
    logger.info("get_environment_id result: %s", result)
    edges = result.get("data", {}).get("environments", {}).get("edges", [])
    if edges:
        return edges[0]["node"]["id"]
    return ""

async def set_variables(project_id: str, service_id: str, environment_id: str, variables: dict) -> bool:
    query = """
    mutation variableCollectionUpsert($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
    }
    """
    payload = {
        "input": {
            "projectId": project_id,
            "serviceId": service_id,
            "environmentId": environment_id,
            "variables": {k: str(v) for k, v in variables.items()}
        }
    }
    logger.info("Setting variables for project=%s service=%s vars=%s",
                project_id, service_id, list(variables.keys()))
    result = await graphql(query, payload)
    logger.info("set_variables result: %s", result)
    return True

async def trigger_deployment(project_id: str, service_id: str) -> str:
    environment_id = await get_environment_id(project_id)
    query = """
    mutation serviceInstanceDeploy($serviceId: String!, $environmentId: String!) {
        serviceInstanceDeploy(serviceId: $serviceId, environmentId: $environmentId)
    }
    """
    variables = {
        "serviceId": service_id,
        "environmentId": environment_id,
    }
    logger.info("Triggering deployment for service=%s", service_id)
    result = await graphql(query, variables)
    logger.info("trigger_deployment result: %s", result)
    return str(result)

async def get_service_url(project_id: str, service_id: str) -> str:
    """Get public URL of deployed service."""
    environment_id = await get_environment_id(project_id)
    query = """
    query getServiceDomain($projectId: String!, $serviceId: String!, $environmentId: String!) {
        domains(projectId: $projectId, serviceId: $serviceId, environmentId: $environmentId) {
            serviceDomains {
                domain
            }
        }
    }
    """
    variables = {
        "projectId": project_id,
        "serviceId": service_id,
        "environmentId": environment_id,
    }
    result = await graphql(query, variables)
    logger.info("get_service_url result: %s", result)
    domains = result.get("data", {}).get("domains", {}).get("serviceDomains", [])
    if domains:
        return f"https://{domains[0]['domain']}"
    return ""


async def create_service_domain(project_id: str, service_id: str, environment_id: str) -> str:
    """Create a Railway domain for the service and return the domain."""
    query = """
    mutation serviceDomainCreate($input: ServiceDomainCreateInput!) {
        serviceDomainCreate(input: $input) {
            id
            domain
        }
    }
    """
    variables = {
        "input": {
            "projectId": project_id,
            "serviceId": service_id,
            "environmentId": environment_id,
        }
    }
    result = await graphql(query, variables)
    logger.info("create_service_domain result: %s", result)
    if "errors" in result:
        logger.warning("Domain creation failed: %s", result["errors"])
        return ""
    domain = result.get("data", {}).get("serviceDomainCreate", {}).get("domain", "")
    return f"https://{domain}" if domain else ""


async def deploy_shop_bot(
    client_name: str,
    slug: str,
    bot_token: str,
    admin_ids: str,
    cloudinary_cloud: str,
    cloudinary_key: str,
    cloudinary_secret: str,
    saas_platform_url: str,
) -> dict:
    project_name = f"shop-{slug}"

    # 1. Create project
    project_id = await create_project(project_name)
    await asyncio.sleep(2)

    # 2. Get environment ID
    environment_id = await get_environment_id(project_id)
    await asyncio.sleep(1)

    # 3. Create Postgres first
    pg_service_id = await create_postgres(project_id, environment_id)
    await asyncio.sleep(2)

    # 4. Create shop_bot service WITHOUT deploying yet
    query = """
    mutation serviceCreate($input: ServiceCreateInput!) {
        serviceCreate(input: $input) {
            id
            name
        }
    }
    """
    variables = {
        "input": {
            "projectId": project_id,
            "name": "shop_bot",
            "source": {
                "repo": GITHUB_REPO,
            }
        }
    }
    result = await graphql(query, variables)
    service_id = result["data"]["serviceCreate"]["id"]
    await asyncio.sleep(2)

    # 5. Set ALL variables before first deploy
    webhook_url = f"https://shop-{slug}.up.railway.app"
    env_vars = {
        "BOT_TOKEN": bot_token,
        "ADMIN_IDS": admin_ids,
        "WEBHOOK_URL": webhook_url,
        "PUBLIC_BASE_URL": webhook_url,
        "LOCAL_POLLING": "1",
        "CLOUDINARY_CLOUD_NAME": cloudinary_cloud,
        "CLOUDINARY_API_KEY": cloudinary_key,
        "CLOUDINARY_API_SECRET": cloudinary_secret,
        "SAAS_PLATFORM_URL": saas_platform_url,
        "SAAS_CLIENT_SLUG": slug,
        "DATABASE_URL": "postgresql://postgres:postgres123@postgres.railway.internal:5432/railway",
    }
    await set_variables(project_id, service_id, environment_id, env_vars)
    await asyncio.sleep(2)

    # 6. Now trigger deployment with variables already set
    await trigger_deployment(project_id, service_id)
    await asyncio.sleep(3)

    # 7. Create domain for the service
    await asyncio.sleep(5)
    url = await create_service_domain(project_id, service_id, environment_id)
    if not url:
        url = await get_service_url(project_id, service_id)

    return {
        "project_id": project_id,
        "service_id": service_id,
        "url": url,
    }
