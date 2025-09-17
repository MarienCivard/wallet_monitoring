# DeFi Multi-Wallet Monitor

Application **Streamlit** pour **monitorer les positions DeFi de plusieurs wallets** (Morpho Blue) et, à terme, **suivre leurs rendements** (borrow rate, LTV, agrégations, etc.).

## Pourquoi ?
- Avoir **une seule interface** pour suivre l’exposition **par wallet** et une **vue consolidée**.
- Visualiser rapidement **collatéral (Supply = Collateral)**, **borrow**, **LTV** et **borrow rate**.
- Préparer le terrain pour le **suivi des rendements** (APY/APR consolidés, historique, fees, PnL).

## Ce que fait l’app (aujourd’hui)
- **Multi-wallet** : saisissez plusieurs adresses, une par ligne.
- **Per-wallet & Consolidated** : tableau détaillé et agrégé par marché.
- **Supply = Collateral** (convention d’affichage) et **Borrow-only** (toggle).
- **Borrow rate actuel** par marché (requêtes GraphQL robustes).
- **USD fiable** : revalorisation optionnelle via **DefiLlama** + normalisation des **decimals**.
- **Multi-chaînes** : Ethereum, Base, Arbitrum (filtrage par sidebar).

## Stack & sources
- **Streamlit**, **Requests**, **Pandas**
- **Morpho GraphQL** (positions par wallet)
- **DefiLlama Prices** (recompute USD, optionnel)

## Roadmap (court terme)
- Suivi étendu des **rendements** (historique, fees payés, APY/APR consolidés).
- Export CSV et alertes (seuils LTV, variations de taux).

> **Disclaimer** : outil d’information. Faites vos propres recherches. Aucune garantie sur l’exactitude des données on-chain/off-chain.
