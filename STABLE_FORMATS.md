# Hadron v3 Stable Format Contract

Publisher: **bxane**  
GitHub: **bxane-dev**  
Repository: **bxane-dev/hadron**

Hadron v3.0 freezes the following public format versions:

| Format | Version |
|---|---:|
| Project | 1 |
| Workspace bundle | 1 |
| Release manifest | 1 |
| Release signature | 1 |
| Study capsule | 1 |
| Reproduction report | 1 |
| Regression report | 1 |
| Regression baseline | 1 |
| Campaign bundle | 1 |
| Campaign reference | 1 |
| Campaign run | 1 |
| Pipeline | 1 |
| Pipeline run | 1 |
| Recovery bundle | 1 |

Compatible readers may add optional fields, but must not reinterpret existing
fields. A breaking structural or semantic change requires incrementing that
format's version.

SQLite schema remains **v12** for Hadron 3.0.0. Existing migration paths remain
supported; v3.0 does not reset user data.
