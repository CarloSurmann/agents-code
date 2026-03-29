"""onboard.py — Interactive CLI to onboard a new customer.

Creates a customer folder with config.yaml and .env from the template,
prompting for the key values.

Usage:
    python onboard.py
    python onboard.py --customer pizzeria-mario
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "customers" / "_template"
CUSTOMERS_DIR = ROOT / "customers"


def slugify(name: str) -> str:
    """Convert a name to a lowercase-hyphenated slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def prompt(question: str, default: str = "", options: list[str] | None = None) -> str:
    """Ask the user a question with optional default and choices."""
    if options:
        opts = " | ".join(options)
        q = f"  {question} [{opts}]"
    elif default:
        q = f"  {question} [{default}]"
    else:
        q = f"  {question}"

    answer = input(q + ": ").strip()
    if not answer and default:
        return default
    return answer


def main():
    parser = argparse.ArgumentParser(description="Onboard a new customer")
    parser.add_argument("--customer", "-c", help="Customer slug (auto-generated if not given)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  AI Agency — Customer Onboarding")
    print("=" * 60 + "\n")

    # --- Identity ---
    company_name = prompt("Company name")
    customer_id = args.customer or slugify(company_name)
    print(f"  → Customer ID: {customer_id}")

    customer_dir = CUSTOMERS_DIR / customer_id
    if customer_dir.exists():
        print(f"\n  ⚠️  Customer '{customer_id}' already exists!")
        overwrite = prompt("Overwrite?", "no", ["yes", "no"])
        if overwrite != "yes":
            print("  Aborted.")
            return

    contact_name = prompt("Main contact name")
    language = prompt("Language", "italian", ["italian", "english", "dutch", "german"])

    # --- Workflow ---
    print()
    workflow = prompt(
        "Workflow",
        "ar-follow-up",
        ["ar-follow-up", "email-follow-up"],
    )

    # --- Model ---
    model = prompt(
        "Model",
        "claude-sonnet-4-6",
        ["claude-sonnet-4-6", "claude-haiku-4-5", "ollama/qwen3.5:9b"],
    )

    # --- Channel ---
    print()
    channel_type = prompt(
        "Channel",
        "whatsapp",
        ["whatsapp", "teams", "telegram", "slack", "console"],
    )

    # --- Accounting ---
    print()
    accounting = prompt(
        "Accounting system",
        "fattureincloud",
        ["fattureincloud", "exact_online", "none"],
    )

    # --- Email ---
    email_provider = prompt(
        "Email provider",
        "gmail",
        ["gmail", "outlook", "mock", "none"],
    )

    user_email = ""
    if email_provider in ("gmail", "outlook"):
        user_email = prompt("Email address (sends from)")

    # --- Create customer folder ---
    print(f"\n  Creating {customer_dir}...")
    shutil.copytree(TEMPLATE_DIR, customer_dir, dirs_exist_ok=True)

    # --- Write config.yaml ---
    config_path = customer_dir / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = f.read()

    replacements = {
        'customer_id: "CUSTOMER_ID"': f'customer_id: "{customer_id}"',
        'company_name: "COMPANY NAME"': f'company_name: "{company_name}"',
        'contact_name: "CONTACT NAME"': f'contact_name: "{contact_name}"',
        'language: "italian"': f'language: "{language}"',
        'workflow: "ar-follow-up"': f'workflow: "{workflow}"',
        'model: "claude-sonnet-4-6"': f'model: "{model}"',
        'type: "whatsapp"': f'type: "{channel_type}"',
        'provider: "fattureincloud"': f'provider: "{accounting}"',
        'provider: "gmail"': f'provider: "{email_provider}"',
        'user_email: ""': f'user_email: "{user_email}"',
    }

    for old, new in replacements.items():
        config = config.replace(old, new, 1)

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config)

    # --- Rename .env.example to .env ---
    env_example = customer_dir / ".env.example"
    env_file = customer_dir / ".env"
    if env_example.exists():
        if env_file.exists():
            os.remove(env_file)
        env_example.rename(env_file)

    # --- Add to .gitignore ---
    gitignore_path = ROOT / ".gitignore"
    gitignore_entry = f"customers/{customer_id}/.env"
    if gitignore_path.exists():
        with open(gitignore_path, "r") as f:
            content = f.read()
        if gitignore_entry not in content:
            with open(gitignore_path, "a") as f:
                f.write(f"\n# Customer secrets\n{gitignore_entry}\n")

    # --- Summary ---
    print("\n" + "=" * 60)
    print(f"  ✅ Customer '{customer_id}' created!")
    print("=" * 60)
    print(f"""
  Next steps:
  1. Fill in credentials:  {customer_dir}/.env
  2. Customize the prompt: {customer_dir}/config.yaml (custom_instructions)
  3. Test locally:         python serve.py --customer {customer_id} --interactive
  4. Run on channel:       python serve.py --customer {customer_id}
  5. Deploy with Docker:   docker-compose up {customer_id}
""")


if __name__ == "__main__":
    main()
