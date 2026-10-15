# Sports agent

Self-improving sports paper trader on Polymarket US, built on the engine of
[Phil](https://github.com/bennyjo/phil) (Apache-2.0) via the
[martincollc/phil](https://github.com/martincollc/phil) fork.

Eight lanes, each with its own $1,000 paper bankroll and its own thresholds:

| Lane | Leagues |
| --- | --- |
| Football | NFL, college football |
| Basketball | NBA, college, WNBA, VTB |
| Baseball | MLB, KBO, NPB, World Baseball Classic |
| Soccer | MLS, Premier League, La Liga, Bundesliga, Serie A, Ligue 1, UCL, UEL, UECL, Liga MX, Brasileirão and more |
| Hockey | NHL |
| Combat sports | UFC, boxing |
| Tennis | ATP, WTA |
| Esports | CS2, League of Legends, Valorant, Rainbow Six |

## Run (operator Mac)

```
python3 core/lane_smoke.py
./loop.sh 1000 45
```

`core/lane_smoke.py` is a read-only live check of the Polymarket US
queries. Thresholds tune themselves inside the limits in
`config/protected.json`; switch a lane off in `config/lanes.json`.

Paper only. No real-money path exists in this repo.
