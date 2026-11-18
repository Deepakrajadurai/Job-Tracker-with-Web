"""Single sign-on: Google, GitHub, and one extra OpenID Connect provider (e.g. Microsoft).

A provider is offered on the login page only when its client id and secret are set:

    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
    GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET
    OIDC_NAME / OIDC_DISCOVERY_URL / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET
"""
from __future__ import annotations

import os

from authlib.integrations.starlette_client import OAuth

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


def build_oauth(env=os.environ) -> tuple[OAuth, dict[str, str]]:
    """Return the OAuth registry and {provider key: button label} for the configured providers."""
    oauth, labels = OAuth(), {}
    if env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET"):
        oauth.register("google", client_id=env["GOOGLE_CLIENT_ID"], client_secret=env["GOOGLE_CLIENT_SECRET"],
                       server_metadata_url=GOOGLE_DISCOVERY, client_kwargs={"scope": "openid email profile"})
        labels["google"] = "Google"
    if env.get("GITHUB_CLIENT_ID") and env.get("GITHUB_CLIENT_SECRET"):
        oauth.register("github", client_id=env["GITHUB_CLIENT_ID"], client_secret=env["GITHUB_CLIENT_SECRET"],
                       access_token_url="https://github.com/login/oauth/access_token",
                       authorize_url="https://github.com/login/oauth/authorize",
                       api_base_url="https://api.github.com/",
                       client_kwargs={"scope": "read:user user:email"})
        labels["github"] = "GitHub"
    if env.get("OIDC_DISCOVERY_URL") and env.get("OIDC_CLIENT_ID") and env.get("OIDC_CLIENT_SECRET"):
        oauth.register("oidc", client_id=env["OIDC_CLIENT_ID"], client_secret=env["OIDC_CLIENT_SECRET"],
                       server_metadata_url=env["OIDC_DISCOVERY_URL"],
                       client_kwargs={"scope": "openid email profile"})
        labels["oidc"] = env.get("OIDC_NAME", "Single sign-on")
    return oauth, labels


async def fetch_identity(oauth: OAuth, provider: str, request) -> dict:
    """Finish the login at the provider and return {subject, email, name, email_verified}."""
    client = oauth.create_client(provider)
    token = await client.authorize_access_token(request)
    if provider == "github":
        profile = (await client.get("user", token=token)).json()
        emails = (await client.get("user/emails", token=token)).json()
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        return {"subject": profile["id"], "email": (primary or {}).get("email", ""),
                "name": profile.get("name") or profile.get("login") or "", "email_verified": primary is not None}
    info = token.get("userinfo") or await client.userinfo(token=token)
    return {"subject": info["sub"], "email": info.get("email", ""), "name": info.get("name", ""),
            "email_verified": bool(info.get("email_verified"))}
