# FAQ about running hazard calculations

### How do I export the hazard curves/maps/uhs for each realization?

By default the engine only exports statistical results, i.e. the mean
hazard curves/maps/uhs. If you want the individual results you must set
`individual_curves=true` in the job.ini files. Please take care: if you have
thousands of realizations (which is quite common) the data transfer
and disk space requirements will be thousands of times larger than
just returning the mean results: the calculation might fail. This is
why by default `individual_curves` is false.

### Argh, I forgot to set `individual_curves`! Must I repeat the calculation?

No, just set `individual_curves=true` in the job.ini and run
```bash
$ oq engine --run job.ini --reuse-hazard --exports csv
```
The individual outputs will be regenerated by reusing the result of the
previous calculation: it will be a lot faster than repeating the calculation
from scratch.

### Argh, I set the wrong `poes` in the job.ini? Must I repeat the calculation?

No, set the right `poes` in the job.ini and as before run
```bash
$ oq engine --run job.ini --reuse-hazard --exports csv
```
Hazard maps and UHS can be regenerated from an existing calculation
quite efficiently.
