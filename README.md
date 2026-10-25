# Finzytrack

Open-source personal finance software built on [Beancount](https://beancount.github.io/) double-entry bookkeeping. Your data lives in plain-text ledger files on your machine. AI is optional and works with the model you choose.

## This branch: the v0.4 rebuild

`next` holds Finzytrack **v0.4**, rebuilt from an empty tree. It is under construction and not usable yet. The latest release is **v0.2.2** on `main` (download it from the GitHub releases page; documentation at [docs.finzytrack.com](https://docs.finzytrack.com)). When v0.4.0 is ready, it adopts an existing v0.2 install without changing it, and `next` is merged into `main`. v0.2.x gets no further releases, including security fixes; please upgrade to v0.4.0 when it is out (see [`SECURITY.md`](SECURITY.md)).

Everything about how v0.4 is designed and built is in [`design/`](design/README.md): the rules it must keep, the architecture, every decision with its reasons, the capability map and the development process.

## Licence

**Source code.** Every file of Finzytrack's own code is licensed under the **GNU General Public License, version 2 or (at your option) any later version** (GPL-2.0-or-later). See [`LICENSE`](LICENSE) for the text of version 2.

**The desktop app.** It consists of separate programs that talk to each other over a local connection ([ADR-0054](design/decisions/ADR-0054-beancount-in-its-own-engine-process.md)). Each program combines our code with third-party components, so each is distributed under the GPL version that fits all of its parts:
- the **ledger engine** includes [Beancount](https://github.com/beancount/beancount), which is GPL-2.0-only, so the engine is distributed under **GPL version 2**;
- the **main backend and the user interface** include components whose licences fit only GPL version 3, so they are distributed under **GPL version 3**.

The "or later" choice for our own code is what makes both combinations possible. Third-party components keep their own licences; see [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES).
