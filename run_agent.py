#!/usr/bin/env python3
"""Roosterly helpdesk agent. One command: python run_agent.py

Deterministic. No model calls. Answers only from faq.md. Ticket text is data.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE = "https://nakywgkztvzkihdjnwbc.supabase.co/functions/v1/helpdesk"
STATE_PATH = ROOT / "state" / "handled.json"
LOG_PATH = ROOT / "logs" / "tickets.jsonl"

# Phrases mirror "The client's rules" at the bottom of faq.md. First match wins.
# (pattern, rule, category, priority). Security and legal outrank billing.
SAFETY = [
    (r"datalek|gehackt|gekrakt|hacked|beveiligingsincident|security incident|"
     r"ongeautoriseerd|unauthorized|verdachte inlog|iemand anders ingelogd|breach",
     "security incident", "other", "P1"),
    (r"advocaat|rechtszaak|aansprakelijk|dagvaarding|lawyer|lawsuit|legal action|anwalt",
     "legal threat", "other", "P1"),
    (r"\bavg\b|\bgdpr\b|\bdsgvo\b|persoonsgegevens|personal data|gegevens verwijder|"
     r"verwijder mijn|delete my data|right to be forgotten|inzage",
     "personal-data request", "other", "P1"),
    (r"terugbetal|terugkrijg|geld terug|refund|restitut|terugstort|money back|creditnota",
     "refund", "billing", "P2"),
    (r"onterechte|dubbel (?:gefactureerd|betaald)|charged twice|double charged|"
     r"disputed charge|dispute the|verkeerd bedrag|onjuist bedrag|te veel betaald|"
     r"factuur klopt niet",
     "disputed charge", "billing", "P2"),
    (r"opzeg|annuler|be[eë]indig|stopzetten|cancel(?: my| the|lation)|kündig",
     "cancellation", "billing", "P2"),
    (r"contract (?:wijzig|aanpass|change)|wijzig(?:en)? (?:ons |het |our )?contract|"
     r"voorwaarden wijzig|change (?:our|the) contract",
     "contract change", "billing", "P2"),
    (r"overstappen|andere aanbieder|concurrent|we gaan weg|we'?ll leave|we will leave|"
     r"anders stoppen|switch (?:provider|to another)",
     "customer threatening to leave", "other", "P2"),
]

# Per FAQ section, not per ticket. Weight >= 3 and a clear winner is required.
FAQ_KEYS = {
    "F01": [("wachtwoord vergeten", 6), ("wachtwoord", 4), ("forgot password", 6),
            ("password", 3), ("passwort", 4), ("resetlink", 3), ("reset link", 3)],
    "F02": [("medewerker toevoegen", 6), ("nieuwe medewerker", 6), ("add employee", 6),
            ("new employee", 5), ("mitarbeiter anlegen", 5), ("mitarbeiter hinzufügen", 5),
            ("contracturen", 4), ("contract hours", 4), ("uitnodigen", 3)],
    "F03": [("ruil aanbieden", 6), ("dienst ruilen", 5), ("ruilen", 4), ("shift swap", 5),
            ("swap a shift", 5), ("schicht tauschen", 5), ("schichttausch", 5)],
    "F04": [("nmbrs", 5), ("loket", 4), ("afas", 4), ("salaris", 3), ("payroll", 4),
            ("uren export", 4), ("export hours", 4), ("koppelingen", 3)],
    "F05": [("wat kost", 4), ("prijs", 3), ("price", 3), ("per maand", 3), ("per month", 3),
            ("upgrade", 3), ("abonnement", 2), ("plus", 2), ("basis", 2)],
    "F06": [("factuur", 4), ("facturen", 4), ("invoice", 4), ("btw-nummer", 4),
            ("bedrijfsgegevens", 4), ("vat number", 4), ("rechnungen", 4)],
    "F07": [("nieuwe vestiging", 5), ("vestiging", 4), ("meerdere vestigingen", 5),
            ("multiple location", 5), ("standort", 3), ("locations", 3)],
    "F08": [("taal wijzigen", 5), ("change language", 5), ("profiel", 2), ("sprache", 3),
            ("in het engels", 4), ("in het duits", 4), ("app language", 4)],
    "F09": [("frankfurt", 5), ("verwerkersovereenkomst", 5), ("waar staan", 4),
            ("data stored", 5), ("where is our data", 5), ("where is my data", 5),
            ("opgeslagen", 3)],
    "F10": [("feature request", 5), ("roadmap", 5), ("suggestie", 4), ("idee", 3),
            ("zou fijn", 4), ("feature", 3), ("wunsch", 3)],
    "F11": [("status.roosterly", 5), ("storing", 4), ("outage", 5), ("onderhoud", 4),
            ("ligt eruit", 4), ("downtime", 4), ("maintenance", 3), ("störung", 4)],
    "F12": [("ios", 4), ("android", 4), ("locatie toestemming", 5), ("location permission", 5),
            ("app versie", 3), ("standortfreigabe", 4)],
}

FAQ_CATEGORY = {
    "F01": "question", "F02": "question", "F03": "question", "F04": "question",
    "F05": "billing", "F06": "billing", "F07": "question", "F08": "question",
    "F09": "question", "F10": "feature_request", "F11": "bug", "F12": "question",
}

# Sentence frames. Every URL, € amount and quoted menu name is copied from faq.md.
NL = {
    "F01": 'Ga naar app.roosterly.nl/login, klik op "Wachtwoord vergeten" en vul je e-mailadres in. De resetlink is 60 minuten geldig. Geen e-mail binnen 10 minuten? Controleer je spammap.',
    "F02": "Ga naar Team → Medewerker toevoegen, vul naam, e-mailadres en contracturen in en klik op Uitnodigen. De medewerker krijgt een e-mail om de app te installeren. Je betaalt alleen voor actieve medewerkers.",
    "F03": 'Open de dienst in de app en tik op "Ruil aanbieden". Collega\'s met dezelfde rol kunnen de ruil accepteren. De beheerder keurt goed onder Rooster → Verzoeken. Goedkeuring staat standaard aan; uitzetten kan onder Instellingen → Rooster.',
    "F04": "Roosterly exporteert goedgekeurde uren naar Nmbrs, Loket.nl en AFAS, of als CSV. Stel dit in onder Instellingen → Koppelingen → Salaris. Exports lopen per loonperiode (week, vier weken of maand). Nmbrs, Loket.nl en AFAS zitten in Plus.",
    "F05": "Basis: €4 per actieve medewerker per maand, één vestiging. Plus: €6 per actieve medewerker per maand, onbeperkt vestigingen, API en de salariskoppelingen. Prijzen zijn exclusief btw. Upgraden kan onder Instellingen → Abonnement; de nieuwe prijs geldt vanaf de volgende factuur.",
    "F06": "Facturen worden op de 1e van elke maand gemaild en staan onder Instellingen → Abonnement → Facturen. Bedrijfsnaam, adres of btw-nummer voor latere facturen wijzig je onder Instellingen → Bedrijfsgegevens.",
    "F07": "Vestigingen horen bij Plus. Voeg er een toe onder Instellingen → Vestigingen → Nieuwe vestiging. Medewerkers kunnen aan meer dan één vestiging gekoppeld worden.",
    "F08": "De webapp en de app zijn er in het Nederlands, Engels en Duits. Elke gebruiker kiest een taal onder Profiel → Taal.",
    "F09": "Alle gegevens staan in de EU (Frankfurt, Duitsland) en vallen onder een verwerkersovereenkomst. Die download je onder Instellingen → Juridisch.",
    "F10": "We lezen elk idee. Verzoeken komen op roadmap.roosterly.nl, waar je kunt stemmen. We beloven geen datum voor een los verzoek.",
    "F11": "Kijk op status.roosterly.nl voor storingen en gepland onderhoud.",
    "F12": "De app werkt op iOS 16 en nieuwer en op Android 10 en nieuwer. Inklokken met locatie vraagt locatietoestemming voor de app.",
}
DE = {
    "F01": 'Gehen Sie zu app.roosterly.nl/login, klicken Sie auf "Wachtwoord vergeten" und geben Sie Ihre E-Mail ein. Der Link gilt 60 Minuten. Keine E-Mail innerhalb von 10 Minuten? Prüfen Sie den Spam-Ordner.',
    "F02": "Gehen Sie zu Team → Medewerker toevoegen, tragen Sie Name, E-Mail und Vertragsstunden ein und klicken Sie auf Uitnodigen. Die Person erhält eine E-Mail zur App. Sie zahlen nur für aktive Mitarbeitende.",
    "F03": 'Öffnen Sie die Schicht in der App und tippen Sie auf "Ruil aanbieden". Kolleginnen mit derselben Rolle können tauschen. Die Leitung genehmigt unter Rooster → Verzoeken. Die Genehmigung ist standardmäßig an, aus unter Instellingen → Rooster.',
    "F04": "Roosterly exportiert freigegebene Stunden nach Nmbrs, Loket.nl und AFAS oder als CSV. Einrichtung unter Instellingen → Koppelingen → Salaris. Exporte laufen je Lohnperiode (Woche, vier Wochen oder Monat). Nmbrs, Loket.nl und AFAS gehören zu Plus.",
    "F05": "Basis: €4 pro aktivem Mitarbeitenden im Monat, ein Standort. Plus: €6 pro aktivem Mitarbeitenden im Monat, unbegrenzte Standorte, API und Lohn-Anbindungen. Preise ohne MwSt. Upgrade unter Instellingen → Abonnement; der neue Preis gilt ab der nächsten Rechnung.",
    "F06": "Rechnungen gehen am 1. jeden Monats per E-Mail raus und stehen unter Instellingen → Abonnement → Facturen. Name, Adresse oder USt-IdNr. ändern Sie unter Instellingen → Bedrijfsgegevens.",
    "F07": "Standorte gehören zu Plus. Anlegen unter Instellingen → Vestigingen → Nieuwe vestiging. Mitarbeitende können mehreren Standorten zugeordnet werden.",
    "F08": "Web-App und mobile App gibt es auf Niederländisch, Englisch und Deutsch. Jede Person wählt die Sprache unter Profiel → Taal.",
    "F09": "Alle Daten liegen in der EU (Frankfurt, Deutschland) unter einem Auftragsverarbeitungsvertrag. Download unter Instellingen → Juridisch.",
    "F10": "Wir lesen jede Idee. Wünsche stehen auf roadmap.roosterly.nl, dort kann man abstimmen. Ein Lieferdatum für einzelne Wünsche gibt es nicht.",
    "F11": "Störungen und Wartung stehen auf status.roosterly.nl.",
    "F12": "Die App läuft auf iOS 16 oder neuer und auf Android 10 oder neuer. Einstempeln mit Standort braucht die Standortfreigabe.",
}

NL_WORDS = set("het een niet van voor met naar ook maar graag hoe waar jullie onze wij dit deze geen wel nog kan kunt kunnen wil factuur rooster medewerker wachtwoord abonnement vestiging collega alstublieft".split())
DE_WORDS = set("ich und der das nicht bitte danke für wie ein eine gerne wir ist passwort rechnung mitarbeiter schicht können ihr ihre und".split())
EN_WORDS = set("the please how would thanks hello invoice password shift your what where when can my we you our from this that with".split())
BUG_RE = re.compile(r"werkt niet|foutmelding|vastloopt|kapot|crash|bug\b|error|does not work|doesn't work|broken|funktioniert nicht|absturz", re.I)
BLOCKED_RE = re.compile(r"kan niet|lukt niet|geen toegang|can't|cannot|blocked|kom niet", re.I)
MANY_RE = re.compile(r"alle medewerkers|alle gebruikers|niemand kan|iedereen|hele team|everyone|all users|alles ligt", re.I)
SPAM_RE = re.compile(r"viagra|bitcoin|crypto|casino|lottery|forex|nigerian|you have won|you've won|weight loss|backlinks|make money fast|seo service", re.I)
PRODUCT_RE = re.compile(r"roosterly|rooster|factuur|medewerker|wachtwoord|invoice|shift|dienst", re.I)
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", re.I)
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
EMPTY = {"", "test", "n/a", "na", "nvt", "?", "...", ".", "-", "hi", "hoi", "hallo", "hello", "geen"}


class AlreadyHandled(Exception):
    pass


class ValidationFailed(Exception):
    pass


def load_dotenv():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def faq_file():
    if os.environ.get("FAQ_PATH"):
        return Path(os.environ["FAQ_PATH"])
    local = ROOT / "faq.md"
    if local.exists():
        return local
    return Path("/cursor/stores/self/internal/brief/faq.md")


def load_faq(path):
    parts = re.split(r"\n## (F\d+)", path.read_text(encoding="utf-8"))
    found = {}
    for i in range(1, len(parts), 2):
        lines = parts[i + 1].split("\n---")[0].strip().splitlines()
        found[parts[i]] = "\n".join(lines[1:]).strip()
    if set(found) != set(FAQ_KEYS):
        raise SystemExit(f"faq.md sections {sorted(found)} != {sorted(FAQ_KEYS)}")
    return found


def redact(text):
    return CARD_RE.sub("[redacted]", IBAN_RE.sub("[redacted]", text))


def detect_language(text):
    words = re.findall(r"[a-zA-Zà-ÿ]+", text.lower())
    scores = {"nl": 0, "de": 0, "en": 0}
    for word in set(words):
        if word in NL_WORDS:
            scores["nl"] += 1
        if word in DE_WORDS:
            scores["de"] += 1
        if word in EN_WORDS:
            scores["en"] += 1
    best = max(("nl", "en", "de"), key=lambda lang: (scores[lang], lang == "nl"))
    return best if scores[best] else "nl"


def is_empty(subject, body):
    blob = re.sub(r"\s+", " ", f"{subject} {body}").strip().lower()
    if blob in EMPTY or not re.search(r"[a-zA-Zà-ÿ0-9]", blob):
        return True
    return len(re.findall(r"[a-zA-Zà-ÿ]{2,}", blob)) == 0


def is_spam(text):
    return bool(SPAM_RE.search(text)) and not PRODUCT_RE.search(text)


def match_faq(text):
    lowered = text.lower()
    scores = []
    for fid, pairs in FAQ_KEYS.items():
        scores.append((sum(weight for phrase, weight in pairs if phrase in lowered), fid))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    if best[0] < 3:
        return None
    if second[0] >= 3 and best[0] - second[0] < 2:
        return "unsure"
    return best[1]


def reply_text(fid, lang):
    if lang == "en":
        body = FAQ[fid]
    elif lang == "de":
        body = DE[fid]
    else:
        body = NL[fid]
    greet = {"nl": "Hoi", "en": "Hello", "de": "Hallo"}.get(lang, "Hoi")
    return f"{greet},\n\n{body}"


def priority_for(text, fid):
    if MANY_RE.search(text) or (fid == "F11" and BUG_RE.search(text) and MANY_RE.search(text)):
        return "P1"
    if fid == "F11" and BUG_RE.search(text):
        return "P3"
    if fid == "F10":
        return "P4"
    return "P4"


def loose_category(text):
    if BUG_RE.search(text):
        return "bug"
    if re.search(r"factuur|invoice|betalen|prijs|abonnement|refund", text, re.I):
        return "billing"
    if re.search(r"feature|idee|suggestie|roadmap", text, re.I):
        return "feature_request"
    if re.search(r"\bhoe\b|\bhow\b|\bwaar\b|\bwhere\b|\bwie\b", text, re.I):
        return "question"
    return "other"


def loose_priority(text, category):
    if MANY_RE.search(text):
        return "P1"
    if category == "billing" or BLOCKED_RE.search(text):
        return "P2"
    if category == "bug":
        return "P3"
    if category == "feature_request":
        return "P4"
    return "P3"


def decide(subject, body):
    """One decision. Never copies ticket text into the reply, note, or reason."""
    subject, body = subject or "", body or ""
    text = f"{subject}\n{body}"
    lang = detect_language(text)
    if is_empty(subject, body):
        return _pack("close", lang, "other", "P4", "Empty ticket. Closed with no reply.", 0.9)
    for pattern, rule, category, priority in SAFETY:
        if re.search(pattern, text, re.I):
            return _pack(
                "handoff", lang, category, priority,
                f"{rule.capitalize()}. Always handed to a human, even if the help centre mentions it.",
                0.95, note=f"{rule.capitalize()}. Needs a person. Language: {lang}.",
            )
    if is_spam(text):
        return _pack("close", lang, "other", "P4", "Spam. Closed with no reply.", 0.9)
    fid = match_faq(text)
    if fid and fid != "unsure":
        return _pack(
            "reply", lang, FAQ_CATEGORY[fid], priority_for(text, fid),
            f"Answered from FAQ {fid} only. No extra product facts.",
            0.86, faq=fid, reply=reply_text(fid, lang),
        )
    category = loose_category(text)
    why = "No FAQ section matched" if fid is None else "Two FAQ sections scored too close"
    return _pack(
        "handoff", lang, category, loose_priority(text, category),
        f"{why}. Handed to a human instead of guessing.",
        0.45, note=f"Unsure. {why}. Language: {lang}. Category guess: {category}.",
    )


def _pack(action, lang, category, priority, reason, confidence, faq=None, reply=None, note=None):
    return {
        "action": action, "language": lang, "category": category, "priority": priority,
        "reason": reason, "confidence": confidence, "faq": faq,
        "draft_reply": reply, "handoff_note": note,
    }


def format_log(ticket_id, decision):
    rec = {
        "id": ticket_id, "action": decision["action"], "language": decision["language"],
        "category": decision["category"], "priority": decision["priority"],
        "faq": decision.get("faq"), "reason": decision["reason"],
    }
    return redact(json.dumps(rec, ensure_ascii=False))


def handle_all(tickets, state, send):
    """Post at most one decision per id. 409 is recorded, not retried. 422 is not marked done."""
    lines = []
    for ticket in tickets:
        ticket_id = ticket["id"]
        if ticket_id in state:
            continue
        decision = decide(ticket.get("subject") or "", ticket.get("body") or "")
        try:
            send(ticket_id, decision)
        except AlreadyHandled:
            decision = {**decision, "action": "already_handled", "faq": None}
        except ValidationFailed:
            lines.append(format_log(ticket_id, {**decision, "action": "validation_failed"}))
            continue
        state.add(ticket_id)
        lines.append(format_log(ticket_id, decision))
    return lines


class Client:
    def __init__(self, base, key, email, name=None, transport=None, sleep=time.sleep, min_interval=0.25):
        self.base = base.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {key}",
            "X-Candidate-Email": email,
            "Content-Type": "application/json",
        }
        if name:
            self.headers["X-Candidate-Name"] = name
        self.transport = transport or self._urlopen
        self.sleep = sleep
        self.min_interval = min_interval
        self._next = 0.0

    def _urlopen(self, method, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def _header(self, headers, name):
        if headers is None:
            return None
        value = headers.get(name) if hasattr(headers, "get") else None
        if value:
            return value
        for key, item in headers.items():
            if str(key).lower() == name.lower():
                return item[0] if isinstance(item, list) else item
        return None

    def request(self, method, path, payload=None):
        url = self.base + path
        body = None if payload is None else json.dumps(payload).encode()
        seen_503 = 0
        seen_429 = 0
        while True:
            now = time.monotonic()
            if now < self._next:
                self.sleep(self._next - now)
            self._next = time.monotonic() + self.min_interval
            status, headers, raw = self.transport(method, url, self.headers, body)
            if status == 503 and seen_503 < 4:
                seen_503 += 1
                self.sleep(0.4 * (2 ** (seen_503 - 1)))
                continue
            if status == 429 and seen_429 < 5:
                seen_429 += 1
                try:
                    wait = float(self._header(headers, "Retry-After") or 1)
                except (TypeError, ValueError):
                    wait = 1.0
                self.sleep(wait)
                continue
            if status == 409:
                raise AlreadyHandled(raw)
            if status == 422:
                raise ValidationFailed(raw)
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
            return json.loads(raw) if raw else {}

    def list_open(self):
        cursor = None
        while True:
            query = "?limit=10&status=open" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
            data = self.request("GET", "/tickets" + query)
            rows = data if isinstance(data, list) else data.get("tickets") or data.get("data") or []
            yield from rows
            cursor = None if isinstance(data, list) else data.get("next_cursor")
            if not cursor:
                break

    def send(self, ticket_id, decision):
        payload = {
            "language": decision["language"], "category": decision["category"],
            "priority": decision["priority"], "reason": decision["reason"],
            "confidence": decision["confidence"],
        }
        if decision["action"] == "reply":
            payload["draft_reply"] = decision["draft_reply"]
        elif decision["action"] == "handoff":
            payload["handoff_note"] = decision["handoff_note"]
        safe = urllib.parse.quote(ticket_id)
        self.request("POST", f"/tickets/{safe}/{decision['action']}", payload)


def make_client(**kwargs):
    key = os.environ.get("HELPDESK_API_KEY", "")
    email = os.environ.get("HELPDESK_CANDIDATE_EMAIL", "")
    if not key or not email:
        sys.exit("Set HELPDESK_API_KEY and HELPDESK_CANDIDATE_EMAIL. See .env.example.")
    return Client(
        os.environ.get("HELPDESK_BASE_URL", DEFAULT_BASE), key, email,
        os.environ.get("HELPDESK_CANDIDATE_NAME") or None, **kwargs,
    )


def load_state():
    if not STATE_PATH.exists():
        return set()
    return set(json.loads(STATE_PATH.read_text()))


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(state)))


def append_log(line):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main(argv):
    load_dotenv()
    client = make_client()
    if "--reset" in argv:
        client.request("POST", "/practice/reset")
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        print("reset: practice tickets reopened, local state cleared")
        return
    state = load_state()
    count = 0
    for ticket in client.list_open():
        for line in handle_all([ticket], state, client.send):
            save_state(state)
            append_log(line)
            count += 1
            print(line)
    print(f"done: {count} new, {len(state)} remembered")


FAQ = load_faq(faq_file())

if __name__ == "__main__":
    main(sys.argv[1:])
