import argparse
import logging
import os
import sqlite3

import requests
from dotenv import load_dotenv
from .config import Settings, load_products
from .discovery import discover, load_searches, update_catalog
from .monitor import run


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sentinela: buscador e monitor de preços com histórico")
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--dry-run", action="store_true", help="busca e avalia sem alterar arquivos nem publicar")
    options.add_argument("--check-config", action="store_true", help="valida configuração sem acessar rede ou banco")
    options.add_argument("--search", action="store_true", help="busca produtos e atualiza catálogo, sem publicar ou registrar preços")
    parser.add_argument("--manual-only", action="store_true", help="usa apenas produtos cadastrados manualmente")
    args = parser.parse_args()
    if args.search and args.manual_only:
        parser.error("--search não pode ser combinado com --manual-only")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.from_env()
        searches_file = os.getenv("SEARCHES_FILE", "config/searches.json")
        catalog_file = os.getenv("DISCOVERY_FILE", "data/discovered.json")
        if not searches_file or not catalog_file:
            raise ValueError("SEARCHES_FILE e DISCOVERY_FILE não podem estar vazios.")
        manual = load_products(settings.products_file)
        config = None if args.manual_only else load_searches(searches_file)
        if args.check_config:
            logging.info("Configuração válida: %d produtos manuais; %d buscas; Discord %s", len(manual), len(config.searches) if config else 0, "configurado" if settings.webhook else "não configurado")
            return 0 if manual or (config and config.searches) else 1
        discovery_failed = False
        products = manual
        if config and config.searches:
            with requests.Session() as session:
                result = discover(config, session, settings.timeout, settings.user_agent)
            discovery_failed = bool(result.failed)
            discovered = update_catalog(catalog_file, result.products, config.max_products, dry_run=args.dry_run, config=config)
            if args.search:
                for product in result.products:
                    print(f"{product.store} | {product.name}\n{product.url}")
                logging.info("Resultado: %d encontrados nesta busca; %d no catálogo; %d buscas sem correspondência; %d falhas", len(result.products), len(discovered), result.empty, result.failed)
                return 1 if discovery_failed or not result.products else 0
            merged = {product.url: product for product in discovered}
            merged.update({product.url: product for product in manual})
            products = list(merged.values())
        elif args.search:
            logging.warning("Nenhuma busca habilitada.")
            return 1
        summary = run(**vars(settings), dry_run=args.dry_run, products=products)
        return max(summary.exit_code, int(discovery_failed))
    except (OSError, ValueError, sqlite3.Error) as exc:
        logging.error("Falha de configuração ou persistência (%s). Verifique .env, config/*.json e acesso aos dados.", type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
