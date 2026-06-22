"""
Health check — verifies Anthropic API connectivity and agent config.
Run manually or via Docker healthcheck to confirm the bot is ready.
No Robinhood token needed.
"""
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent / "smallcap"
sys.path.insert(0, str(BASE))


def main() -> None:
    print("Running health check…")

    # 1. ANTHROPIC_API_KEY present
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key or not api_key.startswith("sk-"):
        print("  FAIL: ANTHROPIC_API_KEY missing or invalid")
        sys.exit(1)
    print(f"  ANTHROPIC_API_KEY: {'*' * 8}{api_key[-4:]}")

    # 2. agent_config.json exists and has required keys
    config_file = BASE / "agent_config.json"
    if not config_file.exists():
        print("  FAIL: agent_config.json not found")
        print("        Run: python smallcap/setup/init_agent.py")
        sys.exit(1)
    config = json.loads(config_file.read_text())
    required = ["agent_id", "environment_id"]
    for key in required:
        if not config.get(key):
            print(f"  FAIL: agent_config.json missing '{key}'")
            sys.exit(1)
    print(f"  Agent ID: {config['agent_id']}")
    print(f"  Environment: {config['environment_id']}")
    print(f"  Auth mode: {config.get('auth_mode', 'unknown')}")
    if config.get("vault_id"):
        print(f"  Vault: {config['vault_id']}")

    # 3. Anthropic API reachable
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        # List agents — lightweight call to verify API key + connectivity
        agents = client.beta.agents.list()
        agent_ids = [a.id for a in agents.data]
        if config["agent_id"] not in agent_ids:
            print(f"  WARN: agent {config['agent_id']} not found in account — may need re-running init_agent.py")
        else:
            print("  Anthropic API: reachable, agent found")
    except Exception as exc:
        print(f"  FAIL: Anthropic API unreachable — {exc}")
        sys.exit(1)

    print("Health check PASSED")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Health check FAILED: {exc}")
        sys.exit(1)
