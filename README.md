# PAK2

PAK2 is a seed for an agent you grow locally. You give it goals, it executes
work, keeps a visible record of what happened, and becomes more tailored to
you through use.

It starts as a working quickstart and grows into a bespoke system around your
operator guidance, memory, and accumulated skills. PAK2 is source code you run
yourself, not a hosted product.

## See It Run

The fastest path from checkout to a live garden is:

```bash
./pak2 init my-garden
cd my-garden
./pak2 genesis
./pak2 cycle
```

With `./pak2 cycle` running, use another terminal for the interactive operator
surface:

```bash
./pak2 chat
```

Optional read-only observability:

```bash
./pak2 dashboard
```

## Customize It

`./pak2 init` gives you a fresh garden directory with:

- a ready-to-run `CHARTER.md` copied from
  [examples/charter-quickstart.md](examples/charter-quickstart.md)
- `CHARTER.md.example` plus the rest of `examples/` for charter customization,
  including scenario-driven starters for personal admin, research, product
  work, and creative practice
- `PAK2.toml`, written for the new garden
- [PAK2.toml.example](PAK2.toml.example), which shows the full shipped config
  surface: `[runtime]`, `[defaults]`, and `[garden]`

Use `CHARTER.md` to define who the agent is for and what it should optimize
around. Use `PAK2.toml` for your garden's active settings, and
`PAK2.toml.example` as the reference when you want to change the runtime path,
garden-wide driver/model/reasoning defaults, or the filesystem garden name.

`./pak2 init` can prefill the `[defaults]` section with `--default-driver`,
`--default-model`, and `--default-reasoning-effort`.

PAK2 is released under the [MIT License](LICENSE).

## Operator, Garden, Plants

PAK2 uses three plain-language roles:

- The **operator** is the human giving direction, constraints, and oversight.
- The **garden** is the persistent working entity that accepts goals, executes
  them over time, and keeps the record.
- **Plants** are the garden's internal specialists. The gardener plant is the
  default executive faculty; additional plants are commissioned only when the
  garden needs a new capability.

From the outside, you interact with one garden. Plants are how the garden
organizes its own work internally.

## What To Read Next

- [System overview](docs/system/level1.md)
- [Startup contract](docs/system/level2.md)
- [Plant model](docs/plant/level1.md)
- [Goal model](docs/goal/level1.md)
- [Motivation](MOTIVATION.md)
- Charter examples:
  [quickstart](examples/charter-quickstart.md),
  [chief-of-staff](examples/charter-chief-of-staff.md),
  [research-apprentice](examples/charter-research-apprentice.md),
  [product-studio](examples/charter-product-studio.md),
  [creative-practice](examples/charter-creative-practice.md)
