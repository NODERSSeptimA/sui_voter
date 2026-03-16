"""Telegram bot for interactive SUI gas price management.

Uses editMessageText for callback interactions so the entire UI
lives in a single message — no chat spam.
"""

import html
import logging
import threading

import requests
import yaml

from sui_client import (
    get_system_state,
    compute_gas_price,
    extract_trusted_votes,
    submit_vote,
    RPCError,
    CLIError,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{}/{}"
POLL_TIMEOUT = 10


def _read_voted_epoch(path):
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_voted_epoch(path, epoch):
    with open(path, "w") as f:
        f.write(str(epoch))


def save_config(path, config):
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


class TelegramBot:
    """Interactive Telegram bot running alongside the auto-voter daemon."""

    def __init__(self, config, config_path, state_file):
        self.config = config
        self.config_path = config_path
        self.state_file = state_file
        self._token = config["telegram"]["bot_token"]
        self._chat_id = str(config["telegram"]["chat_id"])
        self._stop = threading.Event()
        self._offset = 0
        self._user_state = {}

    # ── Lifecycle ──────────────────────────────────────────────

    def run(self):
        logger.info("Telegram bot started (chat_id=%s)", self._chat_id)
        self._register_commands()
        self._send_menu(self._chat_id)
        while not self._stop.is_set():
            try:
                for update in self._poll():
                    self._route(update)
            except Exception:
                logger.exception("Bot error")
                self._stop.wait(5)
        logger.info("Telegram bot stopped")

    def _register_commands(self):
        self._api("setMyCommands", commands=[
            {"command": "start", "description": "Main menu"},
            {"command": "status", "description": "Epoch status & gas prices"},
            {"command": "vote", "description": "Manual vote"},
            {"command": "trusted", "description": "Manage trusted validators"},
        ])

    def stop(self):
        self._stop.set()

    # ── Telegram API ───────────────────────────────────────────

    def _api(self, method, http_timeout=10, **params):
        url = API_BASE.format(self._token, method)
        try:
            resp = requests.post(url, json=params, timeout=http_timeout)
            return resp.json()
        except requests.RequestException:
            logger.warning("Telegram API %s failed", method, exc_info=True)
            return {}

    def _send(self, chat_id, text, markup=None):
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if markup:
            params["reply_markup"] = markup
        return self._api("sendMessage", **params)

    def _edit(self, chat_id, message_id, text, markup=None):
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if markup:
            params["reply_markup"] = markup
        return self._api("editMessageText", **params)

    def _respond(self, chat_id, msg_id, text, markup=None):
        """Edit existing message if msg_id available, send new otherwise."""
        if msg_id:
            return self._edit(chat_id, msg_id, text, markup)
        return self._send(chat_id, text, markup)

    def _answer_cb(self, cb_id):
        self._api("answerCallbackQuery", callback_query_id=cb_id)

    def _poll(self):
        data = self._api(
            "getUpdates",
            http_timeout=POLL_TIMEOUT + 5,
            offset=self._offset,
            timeout=POLL_TIMEOUT,
        )
        results = data.get("result", [])
        if results:
            self._offset = results[-1]["update_id"] + 1
        return results

    # ── Routing ────────────────────────────────────────────────

    def _authorized(self, chat_id):
        return str(chat_id) == self._chat_id

    def _route(self, update):
        if "callback_query" in update:
            cb = update["callback_query"]
            chat_id = str(cb["message"]["chat"]["id"])
            if not self._authorized(chat_id):
                return
            msg_id = cb["message"]["message_id"]
            self._answer_cb(cb["id"])
            self._on_callback(chat_id, msg_id, cb["data"])
        elif "message" in update:
            msg = update["message"]
            chat_id = str(msg["chat"]["id"])
            if not self._authorized(chat_id):
                return
            text = msg.get("text", "")
            if text.startswith("/start") or text.startswith("/menu"):
                self._send_menu(chat_id)
            elif text.startswith("/status"):
                self._cmd_status_new(chat_id)
            elif text.startswith("/vote"):
                self._cmd_vote_start_new(chat_id)
            elif text.startswith("/trusted"):
                self._cmd_trusted_menu_new(chat_id)
            else:
                self._on_text(chat_id, text)

    def _on_callback(self, chat_id, msg_id, data):
        simple = {
            "status": self._cmd_status,
            "trusted": self._cmd_trusted_menu,
            "vote": self._cmd_vote_start,
            "menu": self._edit_menu,
            "recommend": self._cmd_show_recommended,
            "use_recommended": self._cmd_apply_recommended,
            "enter_addresses": self._cmd_enter_addresses,
            "change_quorum": self._cmd_change_quorum,
            "cancel": self._edit_menu,
        }
        if data in simple:
            simple[data](chat_id, msg_id)
        elif data.startswith("confirm_vote:"):
            self._cmd_vote_execute(chat_id, msg_id, int(data.split(":")[1]))
        elif data.startswith("quorum:"):
            self._cmd_apply_quorum(chat_id, msg_id, int(data.split(":")[1]))

    def _on_text(self, chat_id, text):
        state_info = self._user_state.get(chat_id, {})
        state = state_info.get("state")
        msg_id = state_info.get("msg_id")
        handlers = {
            "waiting_vote": self._on_vote_amount,
            "waiting_addresses": self._on_addresses_input,
        }
        handler = handlers.get(state)
        if handler:
            handler(chat_id, msg_id, text.strip())
        else:
            self._send_menu(chat_id)

    # ── Markup helpers ─────────────────────────────────────────

    def _menu_markup(self):
        return {"inline_keyboard": [
            [{"text": "📊 Epoch Status", "callback_data": "status"}],
            [{"text": "🔄 Trusted Validators", "callback_data": "trusted"}],
            [{"text": "✋ Manual Vote", "callback_data": "vote"}],
        ]}

    def _back(self):
        return {"inline_keyboard": [[{"text": "↩️ Menu", "callback_data": "menu"}]]}

    def _back_trusted(self):
        return {"inline_keyboard": [[{"text": "↩️ Back", "callback_data": "trusted"}]]}

    def _cancel(self):
        return {"inline_keyboard": [[{"text": "❌ Cancel", "callback_data": "cancel"}]]}

    def _send_menu(self, chat_id):
        """Send a NEW menu message (for /start command)."""
        self._user_state.pop(chat_id, None)
        self._send(chat_id, "<b>SUI Gas Voter</b>", markup=self._menu_markup())

    def _edit_menu(self, chat_id, msg_id):
        """Edit existing message to show menu (for callbacks)."""
        self._user_state.pop(chat_id, None)
        self._edit(chat_id, msg_id, "<b>SUI Gas Voter</b>", markup=self._menu_markup())

    # ── Slash command wrappers (send new message, no msg_id) ──

    def _cmd_status_new(self, chat_id):
        self._cmd_status(chat_id, None)

    def _cmd_vote_start_new(self, chat_id):
        self._cmd_vote_start(chat_id, None)

    def _cmd_trusted_menu_new(self, chat_id):
        self._cmd_trusted_menu(chat_id, None)

    # ── Status ─────────────────────────────────────────────────

    def _cmd_status(self, chat_id, msg_id):
        try:
            state = get_system_state(self.config["rpc_url"])
        except RPCError as e:
            self._respond(chat_id, msg_id, f"❌ RPC error: {html.escape(str(e))}", markup=self._back())
            return

        validators = state["activeValidators"]
        epoch = state["epoch"]
        ref_price = state.get("referenceGasPrice", "?")

        prices = [int(v["nextEpochGasPrice"]) for v in validators]
        net_median = compute_gas_price(prices, "median")
        net_avg = compute_gas_price(prices, "average")

        trusted_votes = extract_trusted_votes(validators, self.config["trusted_validators"])
        our_price = None
        if len(trusted_votes) >= self.config["min_quorum"]:
            our_price = compute_gas_price(trusted_votes, self.config["strategy"])

        buckets = {}
        for p in prices:
            buckets[p] = buckets.get(p, 0) + 1
        top = sorted(buckets.items(), key=lambda x: -x[1])[:5]

        lines = [
            f"<b>📊 Epoch {html.escape(str(epoch))}</b>",
            f"Validators: {len(validators)}",
            f"Ref price: {html.escape(str(ref_price))} MIST",
            "",
            "<b>Network:</b>",
            f"  Median: {net_median}  Avg: {net_avg}",
            f"  Min: {min(prices)}  Max: {max(prices)}",
            "",
            "<b>Distribution:</b>",
        ]
        for price, count in top:
            pct = count / len(prices) * 100
            lines.append(f"  {price} — {count} val ({pct:.0f}%)")

        lines.append("")
        lines.append(
            f"<b>Trusted ({len(trusted_votes)}/{len(self.config['trusted_validators'])}):</b>"
        )
        trusted_set = set(self.config["trusted_validators"])
        for v in validators:
            if v["suiAddress"] in trusted_set:
                name = html.escape(v.get("name", v["suiAddress"][:12]))
                lines.append(f"  {name}: {v['nextEpochGasPrice']} MIST")

        if our_price is not None:
            lines.append(
                f"\n<b>Auto-vote: {our_price} MIST</b> ({self.config['strategy']})"
            )
        else:
            lines.append("\n⚠️ Quorum not met")

        self._respond(chat_id, msg_id, "\n".join(lines), markup=self._back())

    # ── Trusted validators ─────────────────────────────────────

    def _cmd_trusted_menu(self, chat_id, msg_id):
        trusted = self.config["trusted_validators"]
        lines = [
            "<b>🔄 Trusted Validators</b>",
            f"Quorum: {self.config['min_quorum']}/{len(trusted)}",
            "",
        ]
        try:
            state = get_system_state(self.config["rpc_url"])
            val_map = {v["suiAddress"]: v for v in state["activeValidators"]}
            for addr in trusted:
                v = val_map.get(addr)
                if v:
                    name = html.escape(v.get("name", "?"))
                    lines.append(f"  {name} — {v['nextEpochGasPrice']} MIST")
                else:
                    lines.append("  ⚠️ Not in active set")
                lines.append(f"  <code>{html.escape(addr[:24])}...</code>")
        except RPCError:
            for addr in trusted:
                lines.append(f"  <code>{html.escape(addr[:24])}...</code>")

        self._respond(chat_id, msg_id, "\n".join(lines), markup={"inline_keyboard": [
            [{"text": "📋 Recommended", "callback_data": "recommend"}],
            [{"text": "✏️ Enter addresses", "callback_data": "enter_addresses"}],
            [{"text": "🔢 Change quorum", "callback_data": "change_quorum"}],
            [{"text": "↩️ Menu", "callback_data": "menu"}],
        ]})

    def _cmd_show_recommended(self, chat_id, msg_id):
        try:
            state = get_system_state(self.config["rpc_url"])
        except RPCError as e:
            self._edit(chat_id, msg_id, f"❌ RPC error: {html.escape(str(e))}", markup=self._back())
            return

        validators = state["activeValidators"]
        ref_price = int(state.get("referenceGasPrice", "0"))

        scored = []
        for v in validators:
            price = int(v["nextEpochGasPrice"])
            vp = int(v.get("votingPower", "0"))
            scored.append({
                "name": v.get("name", v["suiAddress"][:16]),
                "address": v["suiAddress"],
                "price": price,
                "vp": vp,
                "active": price != ref_price,
            })
        scored.sort(key=lambda x: (-x["active"], -x["vp"]))
        top = scored[:15]

        active_addrs = [v["address"] for v in top if v["active"]]
        n_use = min(len(active_addrs), 10)

        self._user_state[chat_id] = {
            "state": "recommended",
            "addresses": active_addrs[:n_use],
        }

        lines = [
            f"<b>📋 Recommended</b> (ref: {ref_price} MIST)",
            "✅ = price differs from reference (likely active)",
            "",
        ]
        for i, v in enumerate(top, 1):
            mark = "✅" if v["active"] else "⬜"
            name = html.escape(v["name"])
            lines.append(f"{i}. {mark} <b>{name}</b> — {v['price']} MIST")

        buttons = []
        if active_addrs:
            buttons.append([{
                "text": f"✅ Use top {n_use} active voters",
                "callback_data": "use_recommended",
            }])
        buttons.append([{"text": "↩️ Back", "callback_data": "trusted"}])

        self._edit(chat_id, msg_id, "\n".join(lines), markup={"inline_keyboard": buttons})

    def _cmd_apply_recommended(self, chat_id, msg_id):
        addrs = self._user_state.get(chat_id, {}).get("addresses", [])
        self._user_state.pop(chat_id, None)
        if not addrs:
            self._edit(chat_id, msg_id, "❌ No recommendations available", markup=self._back())
            return
        self.config["trusted_validators"] = addrs
        if self.config["min_quorum"] > len(addrs):
            self.config["min_quorum"] = len(addrs)
        self._persist_config()
        self._edit(
            chat_id, msg_id,
            f"✅ Set {len(addrs)} trusted validators\nQuorum: {self.config['min_quorum']}",
            markup=self._back_trusted(),
        )

    def _cmd_enter_addresses(self, chat_id, msg_id):
        self._user_state[chat_id] = {"state": "waiting_addresses", "msg_id": msg_id}
        self._edit(
            chat_id, msg_id,
            "✏️ Send validator addresses (0x...), one per line or comma-separated:",
            markup=self._cancel(),
        )

    def _on_addresses_input(self, chat_id, msg_id, text):
        self._user_state.pop(chat_id, None)
        addrs = [
            a.strip()
            for a in text.replace(",", "\n").split("\n")
            if a.strip().startswith("0x")
        ]
        if not addrs:
            self._respond(chat_id, msg_id, "❌ No valid 0x addresses found", markup=self._back())
            return
        self.config["trusted_validators"] = addrs
        if self.config["min_quorum"] > len(addrs):
            self.config["min_quorum"] = len(addrs)
        self._persist_config()
        self._respond(
            chat_id, msg_id,
            f"✅ Set {len(addrs)} trusted validators\nQuorum: {self.config['min_quorum']}",
            markup=self._back_trusted(),
        )

    def _cmd_change_quorum(self, chat_id, msg_id):
        n = len(self.config["trusted_validators"])
        current = self.config["min_quorum"]
        rows = []
        row = []
        for i in range(1, n + 1):
            label = f"· {i}" if i == current else str(i)
            row.append({"text": label, "callback_data": f"quorum:{i}"})
            if len(row) == 5:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([{"text": "↩️ Back", "callback_data": "trusted"}])
        self._edit(
            chat_id, msg_id,
            f"<b>🔢 Change Quorum</b>\n\nCurrent: {current}/{n}\nSelect new value:",
            markup={"inline_keyboard": rows},
        )

    def _cmd_apply_quorum(self, chat_id, msg_id, value):
        n = len(self.config["trusted_validators"])
        if value < 1 or value > n:
            self._edit(chat_id, msg_id, f"❌ Must be 1–{n}", markup=self._back_trusted())
            return
        self.config["min_quorum"] = value
        self._persist_config()
        self._edit(
            chat_id, msg_id,
            f"✅ Quorum set to {value}",
            markup=self._back_trusted(),
        )

    # ── Manual vote ────────────────────────────────────────────

    def _cmd_vote_start(self, chat_id, msg_id):
        hint = ""
        try:
            state = get_system_state(self.config["rpc_url"])
            prices = [int(v["nextEpochGasPrice"]) for v in state["activeValidators"]]
            hint = (
                f"Network median: {compute_gas_price(prices, 'median')}\n"
                f"Ref: {state.get('referenceGasPrice', '?')}\n\n"
            )
        except RPCError:
            pass
        self._user_state[chat_id] = {"state": "waiting_vote", "msg_id": msg_id}
        self._respond(
            chat_id, msg_id,
            f"<b>✋ Manual Vote</b>\n\n{hint}Enter gas price (MIST):",
            markup=self._cancel(),
        )

    def _on_vote_amount(self, chat_id, msg_id, text):
        self._user_state.pop(chat_id, None)
        try:
            amount = int(text)
        except ValueError:
            self._respond(chat_id, msg_id, "❌ Enter a number", markup=self._back())
            return
        if amount <= 0:
            self._respond(chat_id, msg_id, "❌ Must be positive", markup=self._back())
            return
        self._respond(chat_id, msg_id, f"Vote <b>{amount} MIST</b>?", markup={"inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"confirm_vote:{amount}"},
                {"text": "❌ Cancel", "callback_data": "cancel"},
            ],
        ]})

    def _cmd_vote_execute(self, chat_id, msg_id, amount):
        self._edit(chat_id, msg_id, f"⏳ Voting {amount} MIST...")
        try:
            tx_info = submit_vote(self.config["sui_bin"], amount)
        except CLIError as e:
            self._edit(chat_id, msg_id, f"❌ Failed: {html.escape(str(e))}", markup=self._back())
            return

        digest = tx_info.get("digest") or "unknown"
        status = tx_info.get("status") or "unknown"
        url = f"https://suiscan.xyz/mainnet/tx/{digest}"

        try:
            state = get_system_state(self.config["rpc_url"])
            _write_voted_epoch(self.state_file, int(state["epoch"]))
        except Exception:
            pass

        self._edit(
            chat_id, msg_id,
            f"✅ <b>Voted {amount} MIST</b>\n"
            f"Digest: <code>{html.escape(digest)}</code>\n"
            f"Status: {html.escape(status)}\n"
            f"<a href=\"{url}\">Explorer</a>",
            markup=self._back(),
        )

    # ── Config persistence ─────────────────────────────────────

    def _persist_config(self):
        try:
            save_config(self.config_path, self.config)
            logger.info("Config saved to %s", self.config_path)
        except Exception:
            logger.exception("Failed to save config")
