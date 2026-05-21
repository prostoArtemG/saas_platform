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

async def create_postgres(project_id: str) -> str:
    """Add PostgreSQL to project using Railway template."""
    query = """
    mutation templateDeploy($input: TemplateDeployInput!) {
        templateDeploy(input: $input) {
            workflowId
        }
    }
    """
    variables = {
        "input": {
            "projectId": project_id,
            "templateCode": "postgres",
        }
    }
    result = await graphql(query, variables)
    logger.info("templateDeploy postgres result: %s", result)
    return ""

async def set_variables(project_id: str, service_id: str, variables: dict) -> bool:
    query = """
    mutation variableCollectionUpsert($input: VariableCollectionUpsertInput!) {
        variableCollectionUpsert(input: $input)
    }
    """
    payload = {
        "input": {
            "projectId": project_id,
            "serviceId": service_id,
            "variables": {k: str(v) for k, v in variables.items()}
        }
    }
    logger.info("Setting variables for project=%s service=%s vars=%s",
                project_id, service_id, list(variables.keys()))
    result = await graphql(query, payload)
    logger.info("set_variables result: %s", result)
    return True

async def trigger_deployment(project_id: str, service_id: str) -> str:
    """Trigger a deployment for the service, return deployment ID."""
    query = """
    mutation serviceInstanceDeploy($serviceId: String!, $environmentId: String) {
        serviceInstanceDeploy(serviceId: $serviceId, environmentId: $environmentId)
    }
    """
    variables = {
        "serviceId": service_id,
        "environmentId": None,
    }
    logger.info("Triggering deployment for service=%s", service_id)
    result = await graphql(query, variables)
    logger.info("trigger_deployment result: %s", result)
    return result

async def get_service_url(project_id: str, service_id: str) -> str:
    """Get public URL of deployed service."""
    query = """
    query getServiceDomain($projectId: String!, $serviceId: String!) {
        domains(projectId: $projectId, serviceId: $serviceId) {
            serviceDomains {
                domain
            }
        }
    }
    """
    variables = {
        "projectId": project_id,
        "serviceId": service_id,
    }
    result = await graphql(query, variables)
    domains = result.get("data", {}).get("domains", {}).get("serviceDomains", [])
    if domains:
        return f"https://{domains[0]['domain']}"
    return ""

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
    """
    Full deployment pipeline for a new shop_bot client.
    Returns dict with project_id, service_id, url.
    """
    project_name = f"shop-{slug}"

    # 1. Create project
    project_id = await create_project(project_name)
    await asyncio.sleep(2)

    # 2. Deploy shop_bot from GitHub
    service_id = await create_service_from_github(
        project_id, GITHUB_REPO, "shop_bot"
    )
    await asyncio.sleep(3)

    # 3. Add PostgreSQL
    await create_postgres(project_id)
    await asyncio.sleep(2)

    # 4. Set environment variables
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
    }
    await set_variables(project_id, service_id, env_vars)

    # 5. Trigger deployment
    await trigger_deployment(project_id, service_id)
    await asyncio.sleep(3)

    # 6. Get URL
    await asyncio.sleep(5)
    url = await get_service_url(project_id, service_id)

    return {
        "project_id": project_id,
        "service_id": service_id,
        "url": url or webhook_url,
    }
