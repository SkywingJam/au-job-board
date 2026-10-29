#!/usr/bin/env python3
"""Synthetic offline location-search semantics; no personal or network data."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jobs import locations  # noqa: E402


class LocationSearch(unittest.TestCase):
    def tier(self, raw, query):
        return locations.match_tier(
            locations.parse_listing(raw), locations.parse_target(query))

    def test_seed_and_supported_targets(self):
        metadata, places, postcodes, aliases, capitals = locations._reference()
        self.assertEqual(metadata["reference_version"], "geonames-au-011ef7d2b65ba2a0")
        self.assertEqual(metadata["seed"]["rows"], 18514)
        self.assertIn("carlton", places)
        self.assertIn("3053", postcodes)
        self.assertEqual(aliases["victoria"], "VIC")
        self.assertIn("melbourne", capitals)
        self.assertEqual(locations.parse_target("Australia")["precision"], "country")
        self.assertEqual(locations.parse_target("澳大利亚")["precision"], "country")
        self.assertEqual(locations.parse_target("Vic")["states"],
                         locations.parse_target("Victoria")["states"])

    def test_capital_and_cbd_postcode_are_distinct_listing_precisions(self):
        city = locations.parse_listing("Melbourne VIC")
        cbd = locations.parse_listing("Melbourne City Centre VIC")
        explicit = locations.parse_listing("Melbourne City Centre, VIC 3000")
        split_postcode = locations.parse_listing("Melbourne, VIC, 3000")
        region = locations.parse_listing("Melbourne Western Suburbs VIC")
        self.assertEqual(city["precision"], "city")
        self.assertEqual(city["states"], ["VIC"])
        self.assertEqual(region["precision"], "city")
        self.assertEqual(cbd["precision"], "postcode")
        self.assertEqual(cbd["postcodes"], ["3000"])
        self.assertEqual(explicit["precision"], "postcode")
        self.assertEqual(explicit["postcodes"], ["3000"])
        self.assertEqual(split_postcode["precision"], "postcode")
        self.assertEqual(split_postcode["postcodes"], ["3000"])
        for query in ("Melbourne", "Melbourne VIC", "3000"):
            self.assertEqual(self.tier("Melbourne City Centre VIC", query), "exact")
            self.assertEqual(self.tier("Melbourne VIC", query), "near")

    def test_ambiguous_locality_is_accepted_and_all_matches_are_kept(self):
        target = locations.parse_target("Carlton")
        self.assertEqual(target["precision"], "locality")
        self.assertEqual(target["localities"], {"carlton"})
        self.assertEqual(target["states"], {"NSW", "TAS", "VIC"})
        self.assertEqual(self.tier("Carlton VIC", "Carlton"), "exact")
        self.assertEqual(self.tier("Carlton NSW", "Carlton"), "exact")
        self.assertEqual(self.tier("Carlton TAS", "Carlton"), "exact")
        self.assertEqual(self.tier("Melbourne VIC", "Carlton"), "near")
        self.assertEqual(self.tier("Carlton NSW", "Carlton VIC"), "hidden")
        self.assertEqual(self.tier("Carlton NSW", "3053"), "hidden")
        self.assertEqual(self.tier("Queanbeyan NSW 2620", "2620 NSW"), "exact")
        self.assertEqual(self.tier("Hume ACT 2620", "2620 NSW"), "hidden")

    def test_postcode_block_then_coarse_same_state_then_unknown(self):
        expected = [
            ("Carlton VIC", "exact"),
            ("Carlton South VIC", "exact"),
            ("Melbourne VIC", "near"),
            ("Geelong VIC", "hidden"),
            ("Sydney NSW", "hidden"),
            ("Undisclosed", "unknown"),
        ]
        for listing, tier in expected:
            with self.subTest(listing=listing):
                self.assertEqual(self.tier(listing, "3053"), tier)

    def test_capital_other_victoria_then_unknown(self):
        expected = [
            ("Melbourne City Centre VIC", "exact"),
            ("Carlton VIC", "near"),
            ("Melbourne VIC", "near"),
            ("Sydney NSW", "hidden"),
            ("Undisclosed", "unknown"),
        ]
        for query in ("Melbourne", "3000"):
            for listing, tier in expected:
                with self.subTest(query=query, listing=listing):
                    self.assertEqual(self.tier(listing, query), tier)

    def test_metro_reference_is_verified_abs_derived_data(self):
        manifest, shares = locations._metro()
        self.assertEqual(manifest["publisher"], "Australian Bureau of Statistics")
        self.assertEqual(manifest["license"], "CC BY 4.0")
        self.assertEqual(len(shares), manifest["tsv"]["rows"])
        self.assertEqual(set(manifest["resources"]), {
            "MB_2021_AUST.xlsx", "POA_2021_AUST.xlsx", "Mesh Block Counts, 2021.xlsx"})
        for resource in manifest["resources"].values():
            self.assertRegex(resource["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(resource["url"].startswith("https://www.abs.gov.au/"))
        self.assertEqual({city: item["name"] for city, item in manifest["capitals"].items()}, {
            "Adelaide": "Greater Adelaide", "Brisbane": "Greater Brisbane",
            "Canberra": "Australian Capital Territory", "Darwin": "Greater Darwin",
            "Hobart": "Greater Hobart", "Melbourne": "Greater Melbourne",
            "Perth": "Greater Perth", "Sydney": "Greater Sydney"})
        self.assertEqual(set(manifest["capitals"]), set(locations._reference()[0]["cities"]))
        # Each CBD postcode lies wholly in its own capital area.
        for city, (postcode, _) in ((c.title(), v) for c, v in locations._CBD_POSTCODES.items()):
            self.assertEqual(shares[postcode], (manifest["capitals"][city]["gccsa"], 1000))
        self.assertEqual(shares["3220"][0], "2RVIC")               # Geelong: not Greater Melbourne
        self.assertLess(shares["3644"][1], manifest["min_share_permille"])   # split between areas
        self.assertNotIn("3001", shares)                             # PO-box code has no postal area

    def test_metro_reference_tampering_fails_visibly(self):
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp)
            for name in ("metro-reference.json", "au-postcode-metro.tsv"):
                shutil.copy(locations._DATA / name, copy / name)
            # LF-normalised content with CRLF is accepted, like the seed.
            data = (copy / "au-postcode-metro.tsv").read_bytes()
            (copy / "au-postcode-metro.tsv").write_bytes(data.replace(b"\n", b"\r\n"))
            locations._metro.cache_clear()
            try:
                with patch.object(locations, "_DATA", copy):
                    self.assertEqual(len(locations._metro()[1]), locations._metro()[0]["tsv"]["rows"])
                    locations._metro.cache_clear()
                    (copy / "au-postcode-metro.tsv").write_bytes(data + b"3000\t2GMEL\t1000\tpersons\n")
                    with self.assertRaises(ValueError):
                        locations._metro()
            finally:
                locations._metro.cache_clear()

    def test_every_capital_has_cbd_then_metro_then_fallback_and_hides_outside(self):
        cases = {
            "Sydney": ("Sydney City Centre NSW", "Parramatta NSW", "Newcastle NSW", "NSW"),
            "Melbourne": ("Melbourne City Centre VIC", "Carlton VIC", "Geelong VIC", "VIC"),
            "Brisbane": ("Brisbane City Centre QLD", "Ipswich QLD", "Toowoomba QLD", "QLD"),
            "Adelaide": ("Adelaide City Centre SA", "Elizabeth SA", "Mount Gambier SA", "SA"),
            "Perth": ("Perth City Centre WA", "Fremantle WA", "Bunbury WA", "WA"),
            "Hobart": ("Hobart City Centre TAS", "Kingston TAS", "Launceston TAS", "TAS"),
            "Darwin": ("Darwin City Centre NT", "Palmerston NT", "Katherine NT", "NT"),
            "Canberra": ("Canberra City Centre ACT", "Belconnen ACT", None, "ACT"),
        }
        for city, (cbd, metro, outside, state) in cases.items():
            postcode = locations._CBD_POSTCODES[city.casefold()][0]
            for query in (city, postcode):
                with self.subTest(city=city, query=query):
                    self.assertEqual(self.tier(cbd, query), "exact")
                    self.assertEqual(self.tier(f"{city} {state}", query), "near")   # name is not the CBD
                    self.assertEqual(self.tier(metro, query), "near")
                    if outside:
                        self.assertEqual(self.tier(outside, query), "hidden")
                    self.assertEqual(self.tier(state, query), "unknown")             # state-only
                    self.assertEqual(self.tier("Australia", query), "unknown")
                    self.assertEqual(self.tier("Undisclosed", query), "unknown")
                    self.assertEqual(self.tier("Sydney NSW" if city != "Sydney"
                                               else "Melbourne VIC", query), "hidden")

    def test_metro_boundaries_are_conservative(self):
        self.assertEqual(self.tier("Melbourne VIC 3001", "Melbourne"), "unknown")   # no postal area
        self.assertEqual(self.tier("Melbourne VIC", "Melbourne"), "near")
        self.assertNotEqual(self.tier("Melbourne VIC", "Melbourne"), "exact")
        self.assertEqual(self.tier("Hume ACT 2620", "Canberra"), "unknown")          # shared by two states
        self.assertEqual(self.tier("Queanbeyan NSW", "Canberra"), "hidden")
        self.assertEqual(self.tier("Alice Springs NT", "Darwin"), "unknown")         # postcode shared by NT/SA/WA
        self.assertEqual(self.tier("Gold Coast QLD", "Brisbane"), "unknown")         # state precision only
        self.assertEqual(self.tier("Parramatta NSW", "Sydney"), "near")              # PO-box codes ignored
        self.assertEqual(self.tier("3004", "Melbourne"), "near")
        self.assertEqual(self.tier("3220", "3000"), "hidden")
        self.assertEqual(self.tier("Docklands 3008", "3000"), "near")

    def test_non_capital_searches_keep_their_meaning(self):
        expected = {
            ("Melbourne VIC", "3053"): "near", ("Geelong VIC", "3053"): "hidden",
            ("Carlton VIC", "Carlton"): "exact", ("Melbourne VIC", "Carlton"): "near",
            ("VIC", "3053"): "near", ("Frankston VIC", "VIC"): "exact",
            ("Geelong VIC", "VIC"): "exact", ("Sydney NSW", "VIC"): "hidden",
            ("VIC", "Australia"): "exact", ("Undisclosed", "3053"): "unknown",
        }
        for (listing, query), tier in expected.items():
            with self.subTest(listing=listing, query=query):
                self.assertEqual(self.tier(listing, query), tier)

    def test_state_country_and_first_location_only(self):
        self.assertEqual(self.tier("Carlton VIC", "VIC"), "exact")
        self.assertEqual(self.tier("Melbourne VIC", "Victoria"), "exact")
        self.assertEqual(self.tier("Sydney NSW", "VIC"), "hidden")
        self.assertEqual(self.tier("Undisclosed", "VIC"), "unknown")
        self.assertEqual(self.tier("Sydney NSW", "Australia"), "exact")
        self.assertEqual(self.tier("All Australia", "Australia"), "exact")
        self.assertEqual(locations.parse_target("All Australia")["precision"], "country")
        self.assertEqual(self.tier("Undisclosed", "Australia"), "unknown")
        first = locations.parse_listing("Sydney NSW Hybrid / Melbourne VIC Remote")
        self.assertEqual(first["states"], ["NSW"])
        self.assertEqual(first["city"], "Sydney")
        comma = locations.parse_listing("Sydney NSW, Melbourne VIC")
        self.assertEqual(comma["states"], ["NSW"])
        self.assertEqual(comma["city"], "Sydney")
        for raw in ("Sydney NSW and Melbourne VIC", "Sydney NSW & Melbourne VIC"):
            joined = locations.parse_listing(raw)
            self.assertEqual(joined["states"], ["NSW"])
            self.assertEqual(joined["city"], "Sydney")
        self.assertEqual(locations.parse_listing("Victoria Park, WA")["states"], ["WA"])
        self.assertEqual(locations.parse_listing("South Australia")["states"], ["SA"])
        self.assertEqual(locations.parse_listing("Western Australia")["states"], ["WA"])

    def test_missing_state_and_invalid_search_are_explicit(self):
        self.assertEqual(locations.parse_listing("Carlton")["precision"], "unknown")
        self.assertEqual(locations.parse_listing("2123")["states"], ["NSW"])
        airport = locations.parse_listing("Melbourne Airport VIC 3041")
        self.assertEqual(airport["precision"], "postcode")
        self.assertEqual(airport["postcodes"], ["3041"])
        self.assertEqual(airport["reason"], "postcode_locality_unverified")
        for value in ("Melbourne VIC / Sydney NSW", "Sydney NSW and Melbourne VIC",
                      "Melbourne VIC Remote", "Atlantis"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                locations.parse_target(value)

    def test_known_capital_prefix_suburbs_are_localities(self):
        cases = (
            ("Melbourne Airport VIC", "Melbourne Airport", {"3045"}),
            ("Sydney Olympic Park NSW", "Sydney Olympic Park", {"2127"}),
        )
        for raw, locality, postcodes in cases:
            parsed = locations.parse_listing(raw)
            target = locations.parse_target(raw)
            self.assertEqual(parsed["precision"], "locality")
            self.assertEqual(target["precision"], "locality")
            self.assertEqual(set(parsed["postcodes"]), postcodes)
            self.assertEqual(locations.match_tier(parsed, target), "exact")

    def test_country_only_is_unknown_for_narrow_geography(self):
        country = locations.parse_listing("Australia")
        for query in ("Melbourne", "3000", "3053", "Carlton", "VIC"):
            with self.subTest(query=query):
                self.assertEqual(self.tier("Australia", query), "unknown")

    def test_local_preference_is_pattern_driven(self):
        patterns = [r"\bVIC\b", "melbourne"]
        self.assertTrue(locations.is_local("Melbourne VIC", patterns))
        self.assertTrue(locations.is_local("Sydney NSW / Melbourne VIC", patterns))
        self.assertFalse(locations.is_local("Sydney NSW", patterns))
        self.assertFalse(locations.is_local("Melbourne VIC", [r"["]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
