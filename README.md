# FUTMNG

**FUTMNG** est une base de données dédiée à **FIFA 17 Ultimate Team** et au serveur MNG FUT.

## Version actuelle

`1.0.2`

## Fonctionnalités

- lecture de la base joueurs du serveur FIFA 17 ;
- recherche par nom, Asset ID et Resource ID ;
- filtres par type de carte ;
- noms visuels : **OR RARE**, **LÉGENDES**, **HALL OF FAME**, **FLASHBACK**, etc. ;
- aperçu du joueur et de ses statistiques ;
- détection **PACKABLE**, **SBC**, **COUPE**, marché et exclusif ;
- mise à jour automatique de FUTMNG via les Releases GitHub ;
- chargement des heads **à la demande** depuis `assets/heads` sur GitHub ;
- prise en charge des fichiers Frosty `p<ID>.png` et `p<ID>.dds` ;
- cache local : une head déjà consultée n'est pas retéléchargée ;
- application en lecture seule vis-à-vis du serveur FIFA 17.

## Heads à la demande

FUTMNG ne télécharge pas toutes les images à l'installation. Quand un joueur est sélectionné, FUTMNG cherche en priorité :

```text
p<assetId>.png
p<assetId>.dds
p<resourceId>.png
p<resourceId>.dds
```

Si le fichier n'est pas déjà présent localement, il est récupéré depuis :

```text
Minegamerfrance/FUTMNG → assets/heads/
```

puis enregistré dans :

```text
cache/heads/
```

Le téléchargement se fait en arrière-plan. Les DDS sont affichés grâce à Pillow, installé automatiquement au premier lancement si nécessaire.

## Lancer FUTMNG

Sous Windows, double-clique sur :

`OUVRIR FUTMNG.bat`

FUTMNG essaye de détecter automatiquement le dossier `serveur fifa 17`. Si le serveur n'est pas détecté, utilise **Choisir le serveur**.

## Structure

```text
FUTMNG/
├── app/
├── assets/
│   └── heads/          # heads GitHub p<ID>.png / p<ID>.dds
├── cache/
│   └── heads/          # créé automatiquement chez l'utilisateur
├── config/
├── updater/
├── version.json
└── OUVRIR FUTMNG.bat
```


## v1.0.3 — images des cartes spéciales
- `p<resourceId>.png/.dds` est recherché avant `p<assetId>.png/.dds`.
- Si une carte spéciale utilise encore le portrait de base en cache, FUTMNG le montre provisoirement puis cherche automatiquement son image spéciale sur GitHub.
- Il n’est plus nécessaire de supprimer manuellement le cache après l’ajout d’une nouvelle image spéciale, à condition de relancer FUTMNG si cette image avait déjà été marquée absente pendant la session.
