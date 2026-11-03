---
tags: [prover-verifier-paper]
max_turns: 300
timeout_seconds: 5400
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash, Agent, SendMessage]
---

Can you audit the claims in the paper in trap-paper/? It's LaTeX source (main.tex, compiled main.pdf beside it). The cited works aren't online, so I put their text in trap-paper/cited/, one file per bibliography key. I want to know which claims aren't actually supported by the results or by what the cited papers say.

Input files: trap-paper/main.tex, trap-paper/main.bbl, trap-paper/refs.bib, trap-paper/main.pdf, trap-paper/cited/aldana1998.md, trap-paper/cited/ito2020.md, trap-paper/cited/lee2021.md, trap-paper/cited/okafor2019.md, trap-paper/cited/park2022.md
