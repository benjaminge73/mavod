"""Bot Telegram maVOD V2 — point d'entrée production.

Consomme les services V2 (IntentService, WorkflowService, DownloadWatcher,
UserSessionStore). Plus de `context.user_data` mutable directement : on
passe par `UserSessionStore` thread-safe.

Lancé via `python -m mavod`.
"""

from __future__ import annotations

import asyncio
import html
import time
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)

from mavod.adapters.llm import LLMAdapter
from mavod.adapters.llm.prompts import load_intent_prompt, prompt_hash
from mavod.adapters.qbittorrent import QBittorrentAdapter
from mavod.config import Settings, load_settings
from mavod.domain import ClarificationRequest, Intent
from mavod.exceptions import (
    IntentParseError,
    IntentValidationError,
    MavodError,
)
from mavod.logging_setup import configure_logging, get_logger
from mavod.services import (
    IntentService,
    RankingService,
    SearchService,
    WorkflowService,
)
from mavod.telegram.jobs import DownloadOutcome, DownloadWatcher
from mavod.telegram.state import (
    PendingClarification,
    UserSession,
    UserSessionStore,
)


log = get_logger(__name__)


# ─── Bot context wiring ──────────────────────────────────────────────────────


class BotContext:
    """Container des services partagés. Stocké dans `application.bot_data['ctx']`."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.system_prompt = load_intent_prompt()

        # Adapters partagés
        self.llm = LLMAdapter(settings)
        self.qb = QBittorrentAdapter(settings)

        # Services
        self.intent_service = IntentService(settings, adapter=self.llm)
        self.search_service = SearchService(settings)
        self.ranking_service = RankingService(settings)
        self.workflow_service = WorkflowService(
            settings,
            search=self.search_service,
            ranking=self.ranking_service,
            qb=self.qb,
        )

        # État user thread-safe
        self.sessions = UserSessionStore()

        # Concurrency cap + download lifecycle
        self.workflow_semaphore = asyncio.Semaphore(settings.max_concurrent_workflows)
        self.download_watcher = DownloadWatcher(settings, qb=self.qb)


def _ctx(application_or_context) -> BotContext:
    """Retourne le BotContext depuis une Application ou un CallbackContext."""
    app = (
        application_or_context.application
        if hasattr(application_or_context, "application")
        else application_or_context
    )
    return app.bot_data["ctx"]


def _user_allowed(user_id: int, allowed: frozenset[int]) -> bool:
    """True si le user_id figure dans `TELEGRAM_ALLOWED_USERS`."""
    return user_id in allowed


# ─── Handlers ────────────────────────────────────────────────────────────────


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/start` — message d'accueil."""
    await update.message.reply_text(
        "👋 maVOD bot prêt.\n"
        "Envoie-moi un titre (ex: \"Dune 2021\" ou \"The Bear S03E04\").\n"
        "Si je ne suis pas sûr, je te poserai une question.\n"
        "Commande /reset pour repartir à zéro."
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/help` — liste des commandes."""
    await update.message.reply_text(
        "Commandes :\n"
        "  /start — message d'accueil\n"
        "  /help — cette aide\n"
        "  /search <titre> — recherche directe (ignore l'historique)\n"
        "  /reset — efface la session en cours"
    )


async def reset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/reset` — efface la session + annule les watchers de téléchargement du user."""
    ctx = _ctx(context)
    user_id = update.effective_user.id if update.effective_user else 0
    await ctx.sessions.discard(user_id)
    # Annule aussi les downloads en cours du user (pas de monitoring orphelin)
    cancelled = await ctx.download_watcher.cancel_user(user_id)
    msg = "🔄 Session réinitialisée."
    if cancelled:
        msg += f" {cancelled} surveillance(s) de téléchargement annulée(s)."
    await update.message.reply_text(msg)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/search <titre>` — recherche directe en flushant l'historique."""
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Usage : /search <titre>")
        return
    # Mode direct : flush la session avant pour ignorer toute clarification en cours
    ctx = _ctx(context)
    user_id = update.effective_user.id if update.effective_user else 0
    session = await ctx.sessions.get(user_id)
    async with session.lock:
        session.reset(system_prompt=ctx.system_prompt)
    await _handle_query(update, context, text)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tout message texte (hors commandes) → pipeline intent → workflow."""
    if not update.message or not update.message.text:
        return
    await _handle_query(update, context, update.message.text)


# ─── Pipeline principal ──────────────────────────────────────────────────────


async def _send_typing(bot, chat_id: int) -> None:
    """Indicateur « typing… » best-effort : un hoquet réseau sur cet appel cosmétique
    ne doit pas faire échouer toute la requête (le pipeline réel n'en dépend pas)."""
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except TelegramError as e:
        log.debug("bot.typing_failed", extra={"chat_id": chat_id, "err": str(e)})


async def _handle_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Pipeline complet : ACL → intent (multi-turn) → workflow torrent → message+watcher."""
    ctx = _ctx(context)
    user_id = update.effective_user.id if update.effective_user else 0

    if not _user_allowed(user_id, ctx.settings.telegram_allowed_users):
        await update.message.reply_text("🚫 Non autorisé.")
        log.info("bot.refused", extra={"user_id": user_id})
        return

    await _send_typing(context.bot, update.effective_chat.id)

    session = await ctx.sessions.get(user_id)
    intent = await _parse_intent(ctx, session, text, update)
    if intent is None:
        return

    desc = _format_intent_desc(intent)
    await update.message.reply_text(f"🔎 Recherche : {desc}…")

    async with ctx.workflow_semaphore:
        try:
            result = await asyncio.to_thread(ctx.workflow_service.run, intent)
        except MavodError as e:
            log.exception("bot.workflow_error", extra={"user_id": user_id})
            await update.message.reply_text(f"💥 Erreur : {e}")
            return
        except Exception as e:
            log.exception("bot.workflow_crash", extra={"user_id": user_id})
            await update.message.reply_text(f"💥 Erreur interne : {e}")
            return

    await _send_result(update, context, ctx, result, intent)


async def _parse_intent(
    ctx: BotContext,
    session: UserSession,
    text: str,
    update: Update,
) -> Optional[Intent]:
    """Boucle multi-turn : parse jusqu'à obtenir un Intent ou exit avec clarification."""
    async with session.lock:
        # TTL : si dernière interaction trop ancienne, on flush
        if session.is_expired(ctx.settings.session_ttl_seconds):
            session.reset(system_prompt=ctx.system_prompt)
        session.touch()

        if not session.history:
            session.history.append({"role": "system", "content": ctx.system_prompt})

        user_msg = text.strip()
        pending = session.pending_clarification
        session.pending_clarification = None

        # Expansion réponse numérotée → texte d'option
        if pending and pending.options and user_msg.isdigit():
            idx = int(user_msg) - 1
            if 0 <= idx < len(pending.options):
                user_msg = pending.options[idx]

        # Thread la réponse en tant que tool_result si on attendait une clarification
        if pending and pending.tool_call_id:
            session.history.append({
                "role":         "tool",
                "tool_call_id": pending.tool_call_id,
                "content":      user_msg,
            })
        else:
            session.history.append({"role": "user", "content": user_msg})

        for _ in range(ctx.settings.max_tool_turns):
            try:
                turn = await asyncio.to_thread(
                    ctx.intent_service.parse, session.history,
                )
            except (IntentParseError, IntentValidationError) as e:
                log.warning("bot.intent_error", extra={"text": text, "err": str(e)})
                await update.message.reply_text(f"❓ Je n'ai pas compris : {e}")
                session.reset(system_prompt=ctx.system_prompt)
                return None

            session.history.append(turn.assistant_msg)
            session.truncate_history(ctx.settings.max_history_messages)

            if turn.is_intent:
                # Workflow va démarrer → reset historique (garde system prompt)
                session.reset(system_prompt=ctx.system_prompt)
                return turn.intent

            # Clarification → on park, on envoie la question, on rend la main
            clar: ClarificationRequest = turn.clarification
            reply = f"❓ {clar.question}"
            if clar.options:
                reply += "\n\n" + "\n".join(
                    f"{i+1}. {opt}" for i, opt in enumerate(clar.options)
                )
            await update.message.reply_text(reply)
            session.pending_clarification = PendingClarification(
                question=clar.question,
                options=list(clar.options) if clar.options else None,
                missing_field=clar.missing_field,
                tool_call_id=clar.tool_call_id,
            )
            return None

        # Trop de tours
        await update.message.reply_text(
            "⚠️ Trop de tours de clarification. Utilise /reset puis renvoie ta demande."
        )
        session.reset(system_prompt=ctx.system_prompt)
        return None


def _format_intent_desc(intent: Intent) -> str:
    """Format human-readable d'un Intent (ex. `Dune (2021) — S03E04`)."""
    desc = intent.title
    if intent.year:
        desc += f" ({intent.year})"
    if intent.season:
        desc += f" — S{intent.season:02d}"
        if intent.episode:
            desc += f"E{intent.episode:02d}"
    return desc


async def _send_result(update, context, ctx: BotContext, result, intent: Intent) -> None:
    """Envoie le résultat workflow à l'utilisateur + démarre le watcher si OK."""
    if result.error:
        if "Aucun candidat" in result.error:
            await update.message.reply_text(f"😕 Aucun résultat pour {_format_intent_desc(intent)}")
        else:
            await update.message.reply_text(f"⚠️ {result.error}")
        return

    name = (result.best_choice or {}).get("title") or "?"
    ui_url = ctx.workflow_service.ui_url(result.search_id)
    # parse_mode HTML : le search_id contient des underscores qui cassent une
    # inline link en Markdown legacy → HTML reste robuste (URL non échappée).
    msg = (
        "✅ En cours de téléchargement\n"
        f"🎯 Torrent choisi : {html.escape(name)}\n"
        "🔗 Pour consulter les torrents disponibles : "
        f'<a href="{html.escape(ui_url)}">ma-vod</a>'
    )
    await update.message.reply_text(
        msg, parse_mode="HTML", disable_web_page_preview=True
    )

    # Surveillance du téléchargement
    if result.qb_submit and result.qb_submit.infohash:
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id

        async def _on_done(_uid: int, outcome: DownloadOutcome) -> None:
            await _notify_download_outcome(context.bot, chat_id, outcome)

        await ctx.download_watcher.watch(
            user_id=user_id,
            infohash=result.qb_submit.infohash,
            torrent_name=name,
            on_done=_on_done,
        )


async def _notify_download_outcome(bot, chat_id: int, outcome: DownloadOutcome) -> None:
    """Notification utilisateur quand un download se termine / timeout / etc."""
    if outcome.kind == "complete":
        text = f"📥 Téléchargement terminé : {outcome.name}"
    elif outcome.kind == "timeout":
        text = (
            f"⏱️ Timeout — le torrent {outcome.name} n'est pas encore complet "
            f"(état: {outcome.state}, {outcome.progress*100:.0f}%)."
        )
    elif outcome.kind == "qb_unavailable":
        text = f"⚠️ qBittorrent injoignable pour {outcome.name}."
    elif outcome.kind == "cancelled":
        # Silencieux : déjà notifié par /reset
        return
    else:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        log.warning("bot.notify_failed", extra={"err": str(e)})


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Filet de sécurité PTB : log toute exception non rattrapée + message générique."""
    log.exception("bot.unhandled_error", extra={"err": str(context.error)})
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("💥 Erreur inattendue.")
        except Exception:
            pass


# ─── Factory ─────────────────────────────────────────────────────────────────


def build_application(settings: Optional[Settings] = None) -> Application:
    """Construit l'Application PTB câblée (BotContext + handlers + persistence)."""
    settings = settings or load_settings()

    persistence_path = settings.state_path
    try:
        persistence_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning(
            "bot.persistence_disabled",
            extra={"path": str(persistence_path), "err": str(e)},
        )
        persistence_path = None

    builder = ApplicationBuilder().token(settings.telegram_bot_token)
    if persistence_path is not None:
        builder = builder.persistence(
            PicklePersistence(
                filepath=str(persistence_path),
                # On gère l'état nous-mêmes via UserSessionStore. PicklePersistence
                # n'a plus rien à faire — on désactive tout le stockage. (Sera
                # retiré dans une PR ultérieure quand on aura migré la persistence
                # vers un store maison.)
                store_data=PersistenceInput(
                    user_data=False,
                    chat_data=False,
                    bot_data=False,
                    callback_data=False,
                ),
            )
        )
    app = builder.build()

    # Wire services
    ctx = BotContext(settings)
    app.bot_data["ctx"] = ctx
    log.info(
        "bot.ready",
        extra={
            "model": ctx.llm.model,
            "intent_prompt_hash": prompt_hash(ctx.system_prompt),
            "allowed_users": sorted(settings.telegram_allowed_users),
            "max_concurrent": settings.max_concurrent_workflows,
        },
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("reset", reset_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    return app


def run() -> None:
    """Point d'entrée production : charge settings, configure logs, lance le long-polling."""
    settings = load_settings()
    configure_logging(log_file=settings.log_path)
    log.info("bot.start", extra={"version": "2.0"})
    app = build_application(settings)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
