"""Conservative offline search over published Australian locations.

The reference data is used at query time only. Nothing in this module writes a
database or depends on network/map services.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).with_name("data")
_MODE = re.compile(r"\b(?:remote|hybrid|on[\s-]?site|work from home|wfh)\b", re.I)
_POSTCODE = re.compile(r"\b[0-9]{4}\b")
_FIRST_LOCATION = re.compile(r"\s*(?:/|\||;|\n)\s*")
_CONJUNCTION = re.compile(r"\s+(?:and|&)\s+", re.I)
_CBD_POSTCODES = {
    "adelaide": ("5000", "SA"),
    "brisbane": ("4000", "QLD"),
    "canberra": ("2600", "ACT"),
    "darwin": ("0800", "NT"),
    "hobart": ("7000", "TAS"),
    "melbourne": ("3000", "VIC"),
    "perth": ("6000", "WA"),
    "sydney": ("2000", "NSW"),
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text)).strip()


@lru_cache(maxsize=1)
def _reference():
    """Fail visibly on a broken installation, rather than hiding all results."""
    metadata = json.loads((_DATA / "location-reference.json").read_text(encoding="utf-8"))
    # A Windows checkout made before .gitattributes pinned LF keeps CRLF after
    # later updates (Git does not rewrite an unchanged blob). Verify the content,
    # not the line-ending choice of the checkout.
    data = (_DATA / "au-postcodes.tsv").read_bytes().replace(b"\r\n", b"\n")
    if (metadata["schema_version"] != 1 or
            hashlib.sha256(data).hexdigest() != metadata["seed"]["sha256"]):
        raise ValueError("Australian location reference is invalid; restore the bundled data files.")
    places: dict[str, set[tuple[str, str, str]]] = {}
    postcodes: dict[str, set[tuple[str, str]]] = {}
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")), delimiter="\t")
    if reader.fieldnames != ["postcode", "locality", "state"]:
        raise ValueError("Australian location reference has invalid columns.")
    count = 0
    for row in reader:
        postcode, locality, state = row["postcode"], row["locality"], row["state"]
        if not re.fullmatch(r"[0-9]{4}", postcode) or not locality or state not in metadata["states"]:
            raise ValueError("Australian location reference has an invalid row.")
        places.setdefault(_norm(locality), set()).add((postcode, state, locality))
        postcodes.setdefault(postcode, set()).add((locality, state))
        count += 1
    if count != metadata["seed"]["rows"] or not count:
        raise ValueError("Australian location reference has an invalid row count.")
    states = {_norm(code): code for code in metadata["states"]}
    states.update({_norm(name): code for code, name in metadata["states"].items()})
    capitals = {_norm(name): (name, state) for name, state in metadata["cities"].items()}
    return metadata, places, postcodes, states, capitals


@lru_cache(maxsize=1)
def _metro():
    """Postcode -> (ABS GCCSA code, person share in permille), verified like the seed."""
    manifest = json.loads((_DATA / "metro-reference.json").read_text(encoding="utf-8"))
    data = (_DATA / "au-postcode-metro.tsv").read_bytes().replace(b"\r\n", b"\n")
    table = manifest["tsv"]
    if (manifest["schema_version"] != 1 or hashlib.sha256(data).hexdigest() != table["sha256"]):
        raise ValueError("Australian metropolitan reference is invalid; restore the bundled data files.")
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")), delimiter="\t")
    if reader.fieldnames != table["columns"]:
        raise ValueError("Australian metropolitan reference has invalid columns.")
    shares = {}
    for row in reader:
        share = int(row["share_permille"])
        if (not re.fullmatch(r"[0-9]{4}", row["postcode"]) or row["postcode"] in shares
                or row["gccsa"] not in manifest["regions"] or not 0 < share <= 1000):
            raise ValueError("Australian metropolitan reference has an invalid row.")
        shares[row["postcode"]] = (row["gccsa"], share)
    if len(shares) != table["rows"] or not shares:
        raise ValueError("Australian metropolitan reference has an invalid row count.")
    return manifest, shares


def _inside_metro(location: dict, city: str) -> bool | None:
    """True/False when every postcode of the listing is clearly in/out of the
    capital's Greater Capital City Statistical Area; None when undetermined.

    A city-level listing names its own capital. Postcodes without an ABS postal
    area (PO-box and large-volume codes of a locality) are ignored when another
    postcode of the place decides. One shared by several states, or split below
    the minimum share between areas, is never guessed.
    """
    manifest, shares = _metro()
    if location.get("precision") == "city":
        return True if location.get("city") == city else None
    _, _, postcodes, _, _ = _reference()
    wanted = manifest["capitals"][city]["gccsa"]
    verdicts = set()
    for code in location.get("postcodes") or ():
        entry = shares.get(code)
        if entry is None:
            continue
        if (entry[1] < manifest["min_share_permille"]
                or len({state for _, state in postcodes.get(code, ())}) != 1):
            return None
        verdicts.add(entry[0] == wanted)
    return verdicts.pop() if len(verdicts) == 1 else None


def _first(raw: str) -> str:
    head = _FIRST_LOCATION.split(raw, maxsplit=1)[0]
    for separator in _CONJUNCTION.finditer(head):
        probe = _POSTCODE.sub(" ", _MODE.sub(" ", head[:separator.start()]))
        probe, _ = _country(_norm(probe))
        _, state = _split_state(probe)
        if state:
            head = head[:separator.start()]
            break
    source = [piece.strip() for piece in head.split(",")]
    pieces = []
    for index, piece in enumerate(source):
        pieces.append(piece)
        probe = _POSTCODE.sub(" ", _MODE.sub(" ", piece))
        probe, _ = _country(_norm(probe))
        _, state = _split_state(probe)
        if state:
            for following in source[index + 1:]:
                if re.fullmatch(r"[0-9]{4}", following):
                    pieces.append(following)
                    continue
                break
            break
    return ", ".join(part for part in pieces if part)


def _split_state(text: str) -> tuple[str, str | None]:
    """Consume one state/territory alias at an edge; retain the remaining text."""
    _, _, _, aliases, capitals = _reference()
    value = _norm(text)
    if not value:
        return "", None
    if value in aliases:
        return "", aliases[value]
    if value in capitals:
        return value, None
    ordered = sorted(aliases, key=len, reverse=True)
    for alias in ordered:
        if value.endswith(" " + alias):
            return value[:-(len(alias) + 1)].strip(), aliases[alias]
    return value, None


def _country(value: str) -> tuple[str, bool]:
    pattern = (r"(?<!south )(?<!western )\b(?:(?:all|anywhere in|across) )?"
               r"australia(?: wide| only)?\b|(?:澳大利亚|澳洲)")
    found = bool(re.search(pattern, value))
    return re.sub(pattern, " ", value), found


def _capital_prefix(text: str) -> tuple[str, str] | None:
    _, _, _, _, capitals = _reference()
    words = _norm(text)
    for key, value in capitals.items():
        if words == key:
            return value
        if words.startswith(key + " "):
            return value
    return None


def _capital_cbd_name(text: str) -> tuple[str, str] | None:
    _, _, _, _, capitals = _reference()
    words = _norm(text)
    for key, value in capitals.items():
        if words.startswith(key + " "):
            tail = words[len(key) + 1:]
            if re.match(r"^(?:city centre|city center|cbd)(?: |$)", tail):
                return value
    return None


@lru_cache(maxsize=16384)
def _location(raw: str | None) -> dict:
    result = {
        "raw": raw or "",
        "precision": "unknown",
        "reason": "not_in_reference",
        "postcodes": [],
        "states": [],
        "localities": [],
    }
    if not isinstance(raw, str) or not raw.strip():
        return result

    value = _norm(_MODE.sub(" ", _first(raw.strip())))
    value, country = _country(value)
    codes = sorted(set(_POSTCODE.findall(value)))
    value = _norm(_POSTCODE.sub(" ", value))
    name, state = _split_state(value)
    if state:
        result["states"] = [state]

    _, places, postcodes, _, capitals = _reference()
    if len(codes) > 1:
        result["reason"] = "multiple_postcodes_unsplit"
        return result
    if codes:
        postcode = codes[0]
        options = {(locality, code_state) for locality, code_state in postcodes.get(postcode, ())
                   if not state or code_state == state}
        if not options:
            result["reason"] = "postcode_state_conflict" if postcode in postcodes else "unsupported_postcode"
            return result
        capital_cbd = _capital_cbd_name(name)
        if name and capital_cbd:
            city, city_state = capital_cbd
            expected, _ = _CBD_POSTCODES[_norm(city)]
            if expected != postcode or (state and state != city_state):
                result["reason"] = "postcode_state_conflict"
                return result
            options = {(city, city_state)}
        elif name:
            named = {(locality, code_state) for locality, code_state in options
                     if _norm(locality) == name}
            if not named:
                result.update(
                    precision="postcode",
                    reason="postcode_locality_unverified",
                    postcode=postcode,
                    postcodes=[postcode],
                    states=sorted({item_state for _, item_state in options}),
                    localities=[],
                )
                return result
            options = named
        result.update(
            precision="postcode",
            reason="explicit_postcode",
            postcode=postcode,
            postcodes=[postcode],
            states=sorted({item_state for _, item_state in options}),
            localities=sorted({locality for locality, _ in options}) if name else [],
        )
        return result

    if name and state and name in places and name not in capitals:
        options = {(postcode, item_state, locality) for postcode, item_state, locality
                   in places[name] if item_state == state}
        if options:
            result.update(
                precision="locality",
                reason="unique_locality" if len(options) == 1 else "ambiguous_locality",
                postcodes=sorted({postcode for postcode, _, _ in options}),
                states=[state],
                localities=sorted({locality for _, _, locality in options}),
            )
            return result
        result.update(precision="state", reason="unresolved_place_state_only")
        return result

    capital = _capital_prefix(name)
    if capital and (not state or state == capital[1]):
        city, city_state = capital
        tail = name[len(_norm(city)):].strip()
        if tail.startswith(("city centre", "city center", "cbd")):
            postcode, _ = _CBD_POSTCODES[_norm(city)]
            result.update(
                precision="postcode",
                reason="capital_cbd",
                postcode=postcode,
                postcodes=[postcode],
                states=[city_state],
                localities=[city],
            )
        else:
            result.update(precision="city", reason="capital_region", city=city, states=[city_state])
        return result

    # A listing without a state is deliberately unknown. Locality names can be
    # duplicated across states and guessing from the name alone would hide rows.
    if name and state:
        options = {(postcode, item_state, locality) for postcode, item_state, locality
                   in places.get(name, ()) if item_state == state}
        if options:
            result.update(
                precision="locality",
                reason="unique_locality" if len(options) == 1 else "ambiguous_locality",
                postcodes=sorted({postcode for postcode, _, _ in options}),
                states=[state],
                localities=sorted({locality for _, _, locality in options}),
            )
            return result
        result.update(precision="state", reason="unresolved_place_state_only")
        return result

    if state:
        result.update(precision="state", reason="explicit_state")
        return result
    if country:
        result.update(precision="country", reason="country_only")
    return result


def parse_locations(raw: str | None, work_type: str | None = None) -> list[dict]:
    """Return the first listed place as one query-time fact.

    ``work_type`` is accepted for old callers but work mode is deliberately not
    interpreted in v1.3. Multiple-location text is not split for display.
    """
    if raw is not None and not isinstance(raw, str):
        raise TypeError("Location must be text or None.")
    if work_type is not None and not isinstance(work_type, str):
        raise TypeError("Work type must be text or None.")
    return [_location(raw)]


def parse_listing(raw: str | None, work_type: str | None = None) -> dict:
    if raw is not None and not isinstance(raw, str):
        raise TypeError("Location must be text or None.")
    if work_type is not None and not isinstance(work_type, str):
        raise TypeError("Work type must be text or None.")
    return _location(raw)


def parse_target(text: str) -> dict:
    """Parse one supported search term.

    Ambiguous locality names are valid: searching Carlton intentionally returns
    every Carlton in the bundled reference.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter an Australian postcode, suburb, city, state or Australia.")
    if _MODE.search(text) or _FIRST_LOCATION.search(text) or _CONJUNCTION.search(text):
        raise ValueError("Enter one Australian place without a work-mode suffix.")

    value = _norm(text)
    value, country = _country(value)
    codes = sorted(set(_POSTCODE.findall(value)))
    value = _norm(_POSTCODE.sub(" ", value))
    name, state = _split_state(value)
    _, places, postcodes, _, capitals = _reference()

    if len(codes) > 1:
        raise ValueError("Enter one Australian postcode.")
    if codes:
        postcode = codes[0]
        options = {(locality, item_state) for locality, item_state in postcodes.get(postcode, ())
                   if not state or item_state == state}
        if not options:
            raise ValueError("The postcode is not in the Australian reference or conflicts with the state.")
        if name and not any(_norm(locality) == name for locality, _ in options):
            raise ValueError("The locality and postcode do not match.")
        result = {
            "precision": "postcode",
            "raw": text.strip(),
            "postcode": postcode,
            "postcodes": {postcode},
            "states": {item_state for _, item_state in options},
            "localities": {_norm(locality) for locality, _ in options},
        }
        capital = next(((name, state) for name, (cbd, state)
                        in _CBD_POSTCODES.items() if cbd == postcode), None)
        if capital:
            result.update(capital=True, city=capital[0].title())
        return result

    if name and name in places and name not in capitals:
        options = {(postcode, item_state, locality) for postcode, item_state, locality
                   in places[name] if not state or item_state == state}
        if options:
            return {
                "precision": "locality",
                "raw": text.strip(),
                "postcodes": {postcode for postcode, _, _ in options},
                "states": {item_state for _, item_state, _ in options},
                "localities": {_norm(locality) for _, _, locality in options},
            }
        raise ValueError("The locality and state do not match.")

    capital = _capital_prefix(name)
    if capital and (not state or state == capital[1]):
        city, city_state = capital
        postcode, _ = _CBD_POSTCODES[_norm(city)]
        return {
            "precision": "capital",
            "raw": text.strip(),
            "city": city,
            "state": city_state,
            "postcode": postcode,
            "postcodes": {postcode},
            "states": {city_state},
        }

    if not name and state:
        return {
            "precision": "state",
            "raw": text.strip(),
            "state": state,
            "states": {state},
        }

    if name and name in places:
        options = {(postcode, item_state, locality) for postcode, item_state, locality
                   in places[name] if not state or item_state == state}
        if options:
            return {
                "precision": "locality",
                "raw": text.strip(),
                "postcodes": {postcode for postcode, _, _ in options},
                "states": {item_state for _, item_state, _ in options},
                "localities": {_norm(locality) for _, _, locality in options},
            }
        raise ValueError("The locality and state do not match.")

    if country and not name and not state:
        return {"precision": "country", "raw": text.strip()}
    if name and not state and name in capitals:
        raise ValueError("Unsupported capital location.")
    raise ValueError("Use an Australian postcode, suburb, capital city, state or Australia.")


def match_tier(location: dict, target: dict | None) -> str:
    """Return exact/near/unknown for a displayed row, or hidden."""
    if target is None:
        return "exact"
    if location.get("precision") == "unknown":
        return "unknown"
    states = set(location.get("states") or ())
    postcodes = set(location.get("postcodes") or ())
    localities = {_norm(value) for value in location.get("localities") or ()}
    precision = target["precision"]

    if precision == "country":
        return "exact" if states or location.get("precision") == "country" else "unknown"
    if location.get("precision") == "country":
        return "unknown"
    target_states = set(target.get("states") or ())
    if states and target_states and states.isdisjoint(target_states):
        return "hidden"
    if precision == "state":
        if states & target["states"]:
            return "exact"
        return "hidden"
    if precision == "capital" or target.get("capital"):
        # CBD postcode first, then the rest of the capital's metropolitan area;
        # a place that cannot be placed inside or outside it stays last.
        if target["postcode"] in postcodes:
            return "exact"
        if not states & target["states"]:
            return "hidden"
        inside = _inside_metro(location, target["city"])
        return "unknown" if inside is None else ("near" if inside else "hidden")
    if localities & set(target.get("localities") or ()):
        return "exact"
    if postcodes & set(target.get("postcodes") or ()):
        return "exact"
    if states & set(target.get("states") or ()):
        if location.get("precision") in ("city", "state"):
            return "near"
        return "hidden"
    return "hidden"


def classify(location: dict, target: dict | None = None) -> str:
    """Compatibility name for the query-time geographic tier."""
    return match_tier(location, target)


def is_local(raw: str | None, patterns: list[str] | tuple[str, ...] | None) -> bool:
    text = str(raw or "")
    for pattern in patterns or ():
        try:
            if re.search(str(pattern), text, re.I):
                return True
        except re.error:
            continue
    return False
