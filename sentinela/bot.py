"""Bot Discord 24h do Sentinela: comandos slash, monitor e publicação.

Um único processo deve controlar o SQLite e as buscas automáticas. O workflow
do GitHub Actions deve ser desativado SOMENTE depois de validar a implantação.
"""
import asyncio
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from .config import Settings, load_products
from .discovery import discover, load_searches, update_catalog
from .monitor import run
from .selection import PublicationPolicy

LOG = logging.getLogger(__name__)


def _required_id(variable: str) -> int:
    value = os.getenv(variable, "").strip()
    if not value.isdecimal() or int(value) <= 0:
        raise ValueError(f"{variable} deve ser um ID numérico do Discord.")
    return int(value)


def _path(variable: str, default: str) -> Path:
    return Path(os.getenv(variable, default)).expanduser()


def prepare_data() -> None:
    """Na primeira inicialização com volume, importa o histórico do repositório."""
    for variable, default, initial in (
        ("DATABASE_PATH", "data/history.sqlite3", "data/history.sqlite3"),
        ("DISCOVERY_FILE", "data/discovered.json", "data/discovered.json"),
        ("RUN_REPORT_FILE", "data/last_run.json", "data/last_run.json"),
    ):
        destination = _path(variable, default)
        source = Path(initial)
        if destination.resolve() == source.resolve() or destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copy2(source, destination)
            LOG.info("Dados iniciais copiados para %s", destination)


def execute_monitor(publisher) -> tuple[int, dict]:
    """Executa a mesma coleta/seleção do CLI sem depender de webhook."""
    settings = Settings.from_env()
    policy = PublicationPolicy.from_env()
    searches_file = os.getenv("SEARCHES_FILE", "config/searches.json")
    catalog_file = os.getenv("DISCOVERY_FILE", "data/discovered.json")

    manual = load_products(settings.products_file)
    config = load_searches(searches_file)
    products = manual
    observations, source_status = {}, {}
    failed = False
    if config.searches:
        with requests.Session() as session:
            discovered_now = discover(config, session, settings.timeout, settings.user_agent)
        failed = bool(discovered_now.failed)
        observations = discovered_now.observations
        source_status = discovered_now.source_status
        catalog = update_catalog(catalog_file, discovered_now.products, config.max_products, config=config)
        merged = {p.url: p for p in catalog if p.store != "Mercado Livre" or p.id in observations}
        merged.update({p.url: p for p in manual})
        products = list(merged.values())

    summary = run(
        **vars(settings),
        products=products,
        publish_results=True,
        policy=policy,
        observations=observations,
        source_status=source_status,
        publisher=publisher,
    )
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "summary": asdict(summary),
        "sources": source_status,
    }
    target = _path("RUN_REPORT_FILE", "data/last_run.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return max(summary.exit_code, int(failed)), report


def _recent(limit: int) -> list[str]:
    target = _path("DATABASE_PATH", "data/history.sqlite3")
    if not target.exists():
        return []
    with sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT product_id, price_cents, kind, sent_at FROM publications "
            "ORDER BY sent_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    catalog_path = _path("DISCOVERY_FILE", "data/discovered.json")
    names = {}
    if catalog_path.exists():
        for item in json.loads(catalog_path.read_text(encoding="utf-8")).get("products", []):
            names[item["id"]] = item.get("name", item["id"])
    return [
        f"{discord.utils.escape_markdown(names.get(pid, pid))[:95]} — "
        f"R$ {price / 100:.2f} ({kind}) — {sent_at[:16]}"
        for pid, price, kind, sent_at in rows
    ]


def _catalog_matches(term: str, limit: int = 8) -> list[str]:
    catalog_path = _path("DISCOVERY_FILE", "data/discovered.json")
    if not catalog_path.exists():
        return []
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    term = term.casefold().strip()
    matches = []
    for item in data.get("products", []):
        if term in item.get("name", "").casefold():
            matches.append(f"**{discord.utils.escape_markdown(item['name'][:95])}** — <{item['url']}>")
        if len(matches) >= limit:
            break
    return matches


class SentinelaBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.channel_id = _required_id("DISCORD_CHANNEL_ID")
        guild_value = os.getenv("DISCORD_GUILD_ID", "").strip()
        self.guild_id = int(guild_value) if guild_value.isdecimal() and int(guild_value) > 0 else None
        self.auto_scan = os.getenv("BOT_AUTO_SCAN", "false").lower() == "true"
        self.scan_minutes = int(os.getenv("BOT_SCAN_MINUTES", "180"))
        if not 15 <= self.scan_minutes <= 10080:
            raise ValueError("BOT_SCAN_MINUTES deve estar entre 15 e 10080.")
        self._scan_lock = asyncio.Lock()
        self.scan_loop.change_interval(minutes=self.scan_minutes)
        self._register_commands()

    def _register_commands(self) -> None:
        @self.tree.command(name="status", description="Consulta a última execução do Sentinela")
        async def status(interaction: discord.Interaction):
            report_path = _path("RUN_REPORT_FILE", "data/last_run.json")
            if not report_path.exists():
                await interaction.response.send_message("Ainda não existe relatório de execução.", ephemeral=True)
                return
            report = json.loads(report_path.read_text(encoding="utf-8"))
            summary = report.get("summary", {})
            text = (
                f"Última execução: {report.get('observed_at', 'desconhecida')}\n"
                f"Produtos: {summary.get('products', 0)} | Coletados: {summary.get('collected', 0)}\n"
                f"Publicados: {summary.get('published', 0)} | Falhas: {summary.get('failed', 0)}\n"
                f"Monitor automático: {'ativo' if self.auto_scan else 'desativado'}"
            )
            await interaction.response.send_message(text, ephemeral=True)

        @self.tree.command(name="ultimas", description="Mostra as últimas ofertas já publicadas")
        @app_commands.describe(quantidade="Quantidade de publicações (1 a 10)")
        async def ultimas(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 10] = 5):
            rows = await asyncio.to_thread(_recent, quantidade)
            await interaction.response.send_message(
                "\n".join(rows)[:1900] if rows else "Nenhuma publicação registrada.",
                ephemeral=True,
            )

        @self.tree.command(name="buscar", description="Procura um termo no catálogo já coletado")
        @app_commands.describe(termo="Nome ou parte do nome do produto")
        async def buscar(interaction: discord.Interaction, termo: str):
            if not termo.strip():
                await interaction.response.send_message("Informe um termo.", ephemeral=True)
                return
            matches = await asyncio.to_thread(_catalog_matches, termo)
            await interaction.response.send_message(
                ("Resultados do catálogo salvo (não é uma busca ao vivo):\n" + "\n".join(matches))[:1900]
                if matches else "Nenhum produto correspondente no catálogo salvo.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="verificar", description="Executa uma rodada de coleta (administradores)")
        @app_commands.default_permissions(manage_guild=True)
        async def verificar(interaction: discord.Interaction):
            user = interaction.user
            if not isinstance(user, discord.Member) or not user.guild_permissions.manage_guild:
                await interaction.response.send_message("Apenas administradores do servidor podem executar buscas.", ephemeral=True)
                return
            if self._scan_lock.locked():
                await interaction.response.send_message("Uma coleta já está em andamento.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                code, report = await self._scan()
                summary = report["summary"]
                await interaction.followup.send(
                    f"Rodada concluída (código {code}). "
                    f"Coletados: {summary['collected']}; publicados: {summary['published']}; "
                    f"falhas: {summary['failed']}.", ephemeral=True,
                )
            except Exception as exc:
                LOG.exception("Coleta manual falhou (%s)", type(exc).__name__)
                await interaction.followup.send("Não foi possível concluir a coleta; consulte os logs.", ephemeral=True)

    async def setup_hook(self) -> None:
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.scan_loop.start()

    async def on_ready(self) -> None:
        LOG.info("Bot pronto: %s; automático=%s, intervalo=%d min", self.user, self.auto_scan, self.scan_minutes)

    async def _send_payload(self, payload: dict) -> None:
        channel = self.get_channel(self.channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.channel_id)
        if not hasattr(channel, "send"):
            raise RuntimeError("DISCORD_CHANNEL_ID não é um canal de texto enviável.")
        embeds = [discord.Embed.from_dict(data) for data in payload["embeds"]]
        await channel.send(embeds=embeds, allowed_mentions=discord.AllowedMentions.none())

    def _publish_from_thread(self, payload: dict) -> None:
        future = asyncio.run_coroutine_threadsafe(self._send_payload(payload), self.loop)
        try:
            future.result(timeout=60)
        except Exception as exc:
            future.cancel()
            raise requests.RequestException("Falha na publicação pelo bot Discord.") from exc

    async def _scan(self) -> tuple[int, dict]:
        async with self._scan_lock:
            return await asyncio.to_thread(execute_monitor, self._publish_from_thread)

    @tasks.loop(minutes=180)
    async def scan_loop(self) -> None:
        if not self.auto_scan:
            return
        if self._scan_lock.locked():
            LOG.warning("Rodada anterior ainda em execução; intervalo ignorado")
            return
        try:
            code, report = await self._scan()
            LOG.info("Monitor terminou: código=%s publicados=%s", code, report["summary"]["published"])
        except Exception as exc:
            LOG.exception("Monitor falhou (%s)", type(exc).__name__)

    @scan_loop.before_loop
    async def before_scan(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Defina DISCORD_BOT_TOKEN no ambiente.")
    prepare_data()
    bot = SentinelaBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
