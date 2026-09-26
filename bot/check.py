"""Kontrola nastavenia.

  python -m bot.check feeds   # ktoré RSS feedy fungujú a koľko článkov vracajú
  python -m bot.check ig      # token, ID účtu, limit publikovania
  python -m bot.check claude  # modely z config.yaml existujú a volanie prejde
  python -m bot.check pages   # testovací obrázok na GitHub Pages je verejne dostupný
"""
from __future__ import annotations

import sys

from .collect import fetch_feed
from .common import OUT_DIR, load_config


def feeds() -> None:
    for f in load_config()["feeds"]:
        items = fetch_feed(f)
        mark = "OK " if items else "XX "
        newest = items[0]["published"][:16] if items else "-"
        print(f"{mark} {f['name']:<20} {len(items):>3} článkov  najnovší: {newest}  {f['url']}")


def ig() -> None:
    from .instagram import Instagram
    client = Instagram()
    me = client.me()
    print("Účet:", me)
    if str(me.get("user_id")) != str(client.user_id):  # platí len keď je IG_USER_ID nastavené ručne
        print(f"POZOR: IG_USER_ID ({client.user_id}) sa nezhoduje s user_id z tokenu ({me.get('user_id')})")
    print("Limit publikovania:", client.publishing_quota())
    print("Posledné posty:", [m.get("permalink") for m in client.recent_media(3)])


def claude() -> None:
    from .llm import ask_json, client
    models = load_config()["model"]
    for role, name in models.items():
        info = client().models.retrieve(name)
        print(f"Model {role}: {info.id} ({info.display_name}) OK")
    print("Test volania:", ask_json(models["writer"], "Test spojenia.", 'Vráť {"ok": true}'))


def pages() -> None:
    from PIL import Image
    from .hosting import upload
    path = OUT_DIR / "check" / "test.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1350), load_config()["brand"]["background"]).save(path, "JPEG")
    print("Verejná URL:", upload([path], "check")[0])


if __name__ == "__main__":
    cmds = {"feeds": feeds, "ig": ig, "claude": claude, "pages": pages}
    cmds.get(sys.argv[1] if len(sys.argv) > 1 else "feeds", feeds)()
