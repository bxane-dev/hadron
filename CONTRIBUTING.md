# Contributing to Hadron

Hadron is maintained under the `bxane-dev/hadron` repository.

## Ground rules

- Keep Hadron clearly labeled as synthetic/toy particle-physics software.
- Do not present generated output as real accelerator or detector data.
- Preserve deterministic behavior for existing seeded workflows.
- Add or update tests for behavioral changes.
- Run the full test suite before opening a pull request.
- Public artifact format changes must follow `STABLE_FORMATS.md`.
- Never commit release private keys, credentials, tokens, or user databases.

## Development

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Windows release builds use:

```powershell
.\build_windows.bat
```

## Pull requests

Describe:

1. what changed
2. why it changed
3. how it was tested
4. whether any stable file/database format changed

Breaking changes to a frozen public format require an explicit format-version
increment.
