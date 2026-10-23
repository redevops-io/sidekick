# Third-party notices

## Nous Research — Hermes Agent (MIT)

Parts of `sidekick` were ported from Hermes Agent (https://github.com/NousResearch/hermes-agent): the communication-channel
gateway (`sidekick/channels/`), background-subagent launch (`sidekick/planner.py`), atomic batch memory operations
(`sidekick/memory.py`) and skill security scanning (`sidekick/skills.py`). Their original licence applies to that code and
is reproduced here as it requires:

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The rest of `sidekick` is licensed as described in [LICENSE.md](LICENSE.md). Hermes Agent's MIT licence is compatible with
distributing that code inside this repository; it does not relicense the repository, and this repository's licence does not
relicense Hermes Agent.
