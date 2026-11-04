# Security

Report vulnerabilities privately to the maintainers through the repository host's private
vulnerability reporting, not in a public issue. Include the commit, the affected service
(web, mission, vision, signalling, Unreal, TURN) and reproduction steps.

The installed services bind loopback only; Caddy on TCP 80 and TURN on 3478 plus its relay
range are the network-facing exceptions. Model credentials live in `.env`, which is never read
by the SAM container, the browser bundle or the signalling server. See `deploy/README.md` for
the port map and `web/README.md` for the session and evidence access rules.
