"""End-to-end tests for the prover-verifier-paper scripts. Standard library only.

    python3 -m unittest discover -s scripts/tests -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_numbers  # noqa: E402
from _common import TextIndex, extract_json, loose, parse_loc  # noqa: E402

PAPER = """# Mini Paper on Widgets

## Abstract

Our method improves accuracy by 5.0 points over the best baseline and generalizes to all languages.

## 1 Introduction

Smith (2020) showed that X causes Y in every setting.

## 2 Results

<!-- TABLE T1 · caption: Accuracy (%) on the test set. -->
Baseline & 70.0 \\\\
Ours & 73.0 \\\\

The improvement is significant (McNemar exact test, discordant counts 30 vs 18, p < 0.01).

## 3 Limitations

We only test English data.

## References

[smith2020] Smith (2020): A study of X and Y.
"""

SOURCE = """# A study of X and Y
## Results
We find that X is associated with Y in two of three settings; we make no causal claim.
"""


def run(*args, stdin=None, ok=True):
    p = subprocess.run([sys.executable, *map(str, args)], input=stdin, capture_output=True, text=True)
    if ok and p.returncode != 0:
        raise AssertionError(f"{args[0]} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


class CommonTests(unittest.TestCase):
    def test_loose_and_find(self):
        idx = TextIndex("alpha\nConfTok reaches 81.4\\% selective\naccuracy, compared with 72.3\\% for MaxProb.\n")
        self.assertEqual(idx.find("ConfTok reaches 81.4% selective accuracy", "L2")["status"], "ok")
        self.assertEqual(idx.find("ConfTok reaches 81.4% selective accuracy", "L40")["status"], "moved")
        # a changed digit is never "found": fuzzy only returns a hint line, and callers refuse it
        self.assertIn(idx.find("ConfTok reaches 91.4% selective accuracy")["status"], ("fuzzy", "missing"))
        self.assertEqual(idx.find("compared with 72.3% for MaxProb")["line"], 3)
        self.assertEqual(loose("Sel.\\ Acc. \\textbf{81.4}"), "selacc814")
        self.assertEqual(loose("Gómez"), loose("G´omez"))

    def test_ledger_refuses_altered_numbers_and_negations(self):
        import ledger
        with tempfile.TemporaryDirectory() as d:
            audit = Path(d)
            (audit / "paper").mkdir()
            (audit / "paper" / "paper.md").write_text(
                "x\nConfTok reaches 81.4\\% selective accuracy on TriviaQA.\nThe gain is not significant.\n")
            S = ledger.Sources(audit)
            ok, err, _ = ledger.check_anchor(S, {"source": "paper", "quote": "ConfTok reaches 81.4% selective"}, "e")
            self.assertIsNone(err)
            for fake in ("ConfTok reaches 91.4% selective accuracy", "The gain is significant."):
                _, err, _ = ledger.check_anchor(S, {"source": "paper", "quote": fake}, "e")
                self.assertIn("not found verbatim", err)
            _, err, _ = ledger.check_anchor(S, {"source": "paper", "quote": "The gain is significant."}, "e")
            self.assertIn("Closest text", err)

    def test_parse_loc(self):
        self.assertEqual(parse_loc("L412-L414"), (412, 414))
        self.assertEqual(parse_loc("paper.md:12-18"), (12, 18))
        self.assertEqual(parse_loc("L7"), (7, 7))
        self.assertIsNone(parse_loc("abstract"))

    def test_extract_json(self):
        self.assertEqual(extract_json('Here you go:\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('prose {"b": [1, 2]} trailing'), {"b": [1, 2]})

    def test_check_numbers(self):
        self.assertFalse(check_numbers.run(["diff", "81.4", "74.3", "--claimed", "9.1"])["match"])
        self.assertTrue(check_numbers.run(["diff", "81.4", "72.3", "--claimed", "9.1"])["match"])
        r = check_numbers.run(["mcnemar", "41", "24", "--max", "0.01"])
        self.assertFalse(r["match"])
        self.assertAlmostEqual(r["value"], 0.0464, places=3)
        self.assertTrue(check_numbers.run(["mcnemar", "31", "29", "--claimed", "0.90"])["match"])
        self.assertFalse(check_numbers.run(["reduction", "25.7", "18.6", "--min", "33.34"])["match"])
        self.assertIn("error", check_numbers.run(["expr", "__import__('os')"]))
        self.assertIn("error", check_numbers.run(["fisher", "1", "2"]))


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name) / "audit"
        (self.audit / "paper").mkdir(parents=True)
        (self.audit / "paper" / "paper.md").write_text(PAPER)
        (self.audit / "paper" / "meta.json").write_text(json.dumps({"title": "Mini Paper on Widgets", "sections": []}))
        (self.audit / "paper" / "references.json").write_text(json.dumps(
            [{"key": "smith2020", "label": "Smith (2020)", "n": 1, "raw": "Smith. A study of X and Y. 2020.",
              "title": "A study of X and Y", "year": "2020"}]))
        src = Path(self.tmp.name) / "smith.md"
        src.write_text(SOURCE)
        run(SCRIPTS / "refs.py", "add", "--audit", self.audit, "--key", "smith2020", "--file", src)

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, *args, stdin=None, ok=True):
        return run(SCRIPTS / "ledger.py", *args, "--audit", self.audit,
                   stdin=json.dumps(stdin) if isinstance(stdin, (dict, list)) else stdin, ok=ok)

    def state(self):
        return json.loads((self.audit / "ledger.json").read_text())["claims"]

    def test_full_protocol(self):
        claims = Path(self.tmp.name) / "claims.json"
        bad = {"claims": [{"id": "C1", "text": "x", "quote": "Our method triples accuracy everywhere", "loc": "L5",
                           "type": "comparison", "tier": "headline"}]}
        claims.write_text(json.dumps(bad))
        p = self.ledger("init", "--claims", claims, "--fatigue", "2", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("quote not found", p.stdout)

        good = {"claims": [
            {"id": "C1", "text": "The method improves accuracy by 5.0 points over the best baseline.",
             "quote": "Our method improves accuracy by 5.0 points over the best baseline", "loc": "L5",
             "section": "Abstract", "type": "comparison", "tier": "headline"},
            {"id": "C2", "text": "The method generalizes to all languages.",
             "quote": "generalizes to all languages", "loc": "L5", "section": "Abstract",
             "type": "generalization", "tier": "headline"},
            {"id": "C3", "text": "Smith (2020) showed that X causes Y in every setting.",
             "quote": "Smith (2020) showed that X causes Y in every setting.", "loc": "L9",
             "section": "Introduction", "type": "attribution", "tier": "body", "cites": ["smith2020"]},
            {"id": "C4", "text": "The improvement is significant at p < 0.01 (McNemar exact test).",
             "quote": "The improvement is significant (McNemar exact test, discordant counts 30 vs 18, p < 0.01)",
             "loc": "L30", "section": "Results", "type": "statistical", "tier": "body"},
            {"id": "C5", "text": "The method improves accuracy by 5.0 points over the best baseline.",
             "quote": "Our method improves accuracy by 5.0 points", "loc": "L5", "section": "Abstract",
             "type": "comparison", "tier": "headline", "restates": "C1"},
        ]}
        claims.write_text(json.dumps(good))
        p = self.ledger("init", "--claims", claims, "--fatigue", "2", "--batch-size", "4")
        self.assertIn("1 restatements linked", p.stdout)
        self.assertIn("loc corrected", p.stdout)  # C4 quote is on L19, not L30
        self.assertEqual(self.state()["C5"]["state"], "linked")

        p = self.ledger("next")
        self.assertIn("B1-prover-r0.md", p.stdout)
        packet = (self.audit / "packets" / "B1-prover-r0.md").read_text()
        self.assertIn("### C1 · headline · comparison", packet)

        c1 = {"id": "C1", "position": "narrowed", "statement": "The gain over the baseline is 3.0 points, not 5.0.",
              "subclaims": [
                  {"id": "a", "text": "The table reports 73.0 for ours and 70.0 for the baseline.",
                   "evidence": [{"source": "paper", "loc": "L14", "quote": "Baseline & 70.0"},
                                {"source": "paper", "loc": "L15", "quote": "Ours & 73.0 \\\\"}]},
                  {"id": "b", "text": "73.0 - 70.0 = 3.0, not 5.0.", "check": "diff 73.0 70.0 --claimed 5.0",
                   "expect": "match"}],
              "supported_version": "The method improves accuracy by 3.0 points over the baseline.",
              "overclaim": {"kinds": ["magnitude"], "gap": "5.0 stated, 3.0 measured"},
              "reasoning": "Recomputed from the table."}
        c2 = {"id": "C2", "position": "contradicted", "statement": "Only English data were tested.",
              "subclaims": [{"id": "a", "text": "Limitations say only English was tested.",
                             "evidence": [{"source": "paper", "loc": "L24", "quote": "We only test English data."}]}],
              "reasoning": "The paper's own limitation contradicts 'all languages'."}
        c3_bad = {"id": "C3", "position": "narrowed", "statement": "Smith reports an association, not causation.",
                  "subclaims": [{"id": "a", "text": "The intro states the causal version.",
                                 "evidence": [{"source": "paper", "quote": "Smith (2020) showed that X causes Y"}]}],
                  "supported_version": "Smith (2020) found X associated with Y in two of three settings.",
                  "overclaim": {"kinds": ["causal", "scope"], "gap": "causal and 'every setting'"},
                  "reasoning": "r"}
        c4 = {"id": "C4", "position": "contradicted", "statement": "Exact McNemar on 30 vs 18 gives p = 0.11.",
              "subclaims": [{"id": "a", "text": "Discordant counts are 30 and 18.",
                             "evidence": [{"source": "paper", "quote": "discordant counts 30 vs 18, p < 0.01"}]},
                            {"id": "b", "text": "Exact McNemar p exceeds 0.01.", "check": "mcnemar 30 18 --max 0.01",
                             "expect": "mismatch"}],
              "reasoning": "Recomputed."}
        fabricated = {"id": "C2", "position": "supported", "statement": "s",
                      "subclaims": [{"id": "a", "text": "t",
                                     "evidence": [{"source": "paper", "quote": "We evaluate on twelve languages."}]}]}
        p = self.ledger("submit", "--role", "prover", "--batch", "B1", "--check", stdin={"proofs": [c4]})
        self.assertIn("CHECK ONLY — nothing recorded. Would record: C4", p.stdout)
        self.assertEqual(self.state()["C4"]["state"], "await_prover")
        p = self.ledger("submit", "--role", "prover", "--batch", "B1",
                        stdin={"proofs": [c1, fabricated, c3_bad, c4]}, ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("C1: sub-claim b: check", p.stdout)          # expect contradicts the computed result
        self.assertIn("C2: sub-claim a evidence 1: quote not found", p.stdout)
        self.assertIn("C3: attribution claims are proved from the cited source", p.stdout)
        self.assertIn("recorded: C4", p.stdout)

        c1["subclaims"][1]["expect"] = "mismatch"
        c3 = dict(c3_bad)
        c3["subclaims"] = c3_bad["subclaims"] + [
            {"id": "b", "text": "Smith reports association in two of three settings and disclaims causation.",
             "evidence": [{"source": "smith2020",
                           "quote": "X is associated with Y in two of three settings; we make no causal claim"}]}]
        self.ledger("submit", "--role", "prover", "--batch", "B1", stdin={"proofs": [c1, c2, c3]})
        self.assertEqual({k: v["state"] for k, v in self.state().items()},
                         {"C1": "await_verifier", "C2": "await_verifier", "C3": "await_verifier",
                          "C4": "await_verifier", "C5": "linked"})

        self.ledger("next")
        vp = (self.audit / "packets" / "B1-verifier-r1.md").read_text()
        self.assertIn("challenge-first", vp)
        self.assertIn("contradicts the claimed value", vp)

        p = self.ledger("submit", "--role", "verifier", "--batch", "B1", stdin={"verdicts": [
            {"id": "C1", "verdict": "Accept", "reasoning": "fine"},
            {"id": "C2", "verdict": "Challenge", "reasoning": "check", "challenge": "Is there a multilingual "
             "appendix?", "challenged_subclaim": "a"},
            {"id": "C3", "verdict": "Reject", "reasoning": "The prover's narrowed version is still too strong?",
             "counter_evidence": [{"source": "smith2020", "quote": "we make no causal claim"}]},
            {"id": "C4", "verdict": "Challenge", "reasoning": "verify", "challenge": "Are the counts paired?",
             "challenged_subclaim": "b"}]}, ok=False)
        self.assertIn("C1: challenge-first policy", p.stdout)
        self.assertIn("recorded: C2:Challenge C3:Reject C4:Challenge", p.stdout)
        self.ledger("submit", "--role", "verifier", "--batch", "B1", stdin={"verdicts": [
            {"id": "C1", "verdict": "Challenge", "reasoning": "check rows", "challenge": "Is 70.0 the best baseline?",
             "challenged_subclaim": "a"}]})

        self.ledger("next")
        pp = (self.audit / "packets" / "B1-prover-r1.md").read_text()
        self.assertIn("Is 70.0 the best baseline?", pp)
        self.assertNotIn("check rows", pp)  # the verifier's reasoning stays out of the prover's view
        self.ledger("submit", "--role", "prover", "--batch", "B1", stdin={"proofs": [c1, c2, c4]})
        p = self.ledger("submit", "--role", "verifier", "--batch", "B1", stdin={"verdicts": [
            {"id": "C1", "verdict": "Accept", "reasoning": "Only one baseline exists."},
            {"id": "C2", "verdict": "Accept", "reasoning": "No other languages appear."},
            {"id": "C4", "verdict": "Challenge", "reasoning": "still unsure", "challenge": "again?",
             "challenged_subclaim": "a"}]})
        st = self.state()
        self.assertEqual((st["C1"]["outcome"], st["C1"]["label"], st["C1"]["confidence"]), ("ANC", "overclaim", "high"))
        self.assertEqual((st["C2"]["outcome"], st["C2"]["label"]), ("ANC", "contradicted"))
        self.assertEqual((st["C3"]["outcome"], st["C3"]["state"]), ("rejected", "await_adjudicator"))
        self.assertEqual((st["C4"]["outcome"], st["C4"]["state"]), ("fatigue", "await_adjudicator"))

        p = self.ledger("next")
        # C3 was rejected a round earlier, so its packet (A1) is still held; only C4 is newly issued
        self.assertIn("A2-adjudicator.md", p.stdout)
        self.assertIn("in progress (issued, not yet fully submitted): adjudicator C3", p.stdout)
        self.assertIn("C3", (self.audit / "packets" / "A1-adjudicator.md").read_text())
        self.ledger("submit", "--role", "adjudicator", stdin={"verdicts": [
            {"id": "C3", "label": "overclaim", "rationale": "Source reports association only.",
             "supported_version": "Smith (2020) found X associated with Y in two of three settings.",
             "evidence": [{"source": "smith2020", "quote": "X is associated with Y in two of three settings"}],
             "fix": "Say 'associated with ... in two of three settings'."},
            {"id": "C4", "label": "contradicted", "rationale": "Exact McNemar p = 0.11.",
             "checks": [{"check": "mcnemar 30 18 --max 0.01", "expect": "mismatch"}]}]})
        p = self.ledger("next")
        self.assertIn("COMPLETE", p.stdout)

        run(SCRIPTS / "render_report.py", "--audit", self.audit, "--models", "test")
        rep = json.loads((self.audit / "report.json").read_text())
        self.assertEqual(rep["counts"], {"overclaim": 2, "contradicted": 2})
        flags = {f["id"]: f for f in rep["flags"]}
        self.assertEqual(flags["C1"]["severity"], "major")
        self.assertEqual(flags["C2"]["severity"], "critical")
        self.assertEqual(flags["C1"]["also_at"][0]["id"], "C5")
        md = (self.audit / "report.md").read_text()
        self.assertIn("What the evidence supports.", md)
        self.assertIn("P:narrowed → V:Challenge(a) → P:narrowed → V:Accept", md)

    def test_next_does_not_double_issue(self):
        claims = Path(self.tmp.name) / "claims.json"
        claims.write_text(json.dumps([{"id": "C1", "text": "x", "quote": "Our method improves accuracy by 5.0 points",
                                       "loc": "L5", "type": "comparison", "tier": "body"}]))
        self.ledger("init", "--claims", claims)
        self.assertIn("B1-prover-r0.md", self.ledger("next").stdout)
        again = self.ledger("next").stdout
        self.assertIn("nothing new to launch", again)
        self.assertIn("in progress", again)
        self.assertIn("B1-prover-r0.md", self.ledger("next", "--reissue", "B1").stdout)

    def test_cannot_prove_requires_search(self):
        claims = Path(self.tmp.name) / "claims.json"
        claims.write_text(json.dumps([{"id": "C1", "text": "Gains come from long-tail entities.",
                                       "quote": "Our method improves accuracy by 5.0 points", "loc": "L5",
                                       "type": "causal", "tier": "body"}]))
        self.ledger("init", "--claims", claims)
        base = {"id": "C1", "position": "cannot_prove", "blocker": "no_evidence",
                "statement": "No analysis supports this.", "subclaims": [{"id": "a", "text": "No analysis."}],
                "reasoning": "r"}
        p = self.ledger("submit", "--role", "prover", stdin={"proofs": [base]}, ok=False)
        self.assertIn("needs 'searched'", p.stdout)
        base["searched"] = [{"pattern": "long.?tail", "where": "paper", "hits": 2}]
        p = self.ledger("submit", "--role", "prover", stdin={"proofs": [base]})
        self.assertIn("actual 0", p.stdout)  # the ledger re-ran the search and reports the discrepancy


if __name__ == "__main__":
    unittest.main()
