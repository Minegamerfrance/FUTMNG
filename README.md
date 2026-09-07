# FUTMNG

**FUTMNG** est une base de données locale dédiée à **FIFA 17 Ultimate Team** et au serveur MNG FUT.

## Version actuelle

`1.0.0`

## Fonctionnalités

- lecture de la base joueurs du serveur FIFA 17 ;
- recherche par nom, Asset ID et Resource ID ;
- filtres par type de carte ;
- noms visuels propres : **OR RARE**, **LÉGENDES**, **HALL OF FAME**, **FLASHBACK**, etc. ;
- aperçu du joueur et de ses statistiques ;
- détection **PACKABLE** ;
- détection des récompenses **SBC** ;
- détection des récompenses de **COUPE** ;
- statut marché / exclusif ;
- application actuellement en **lecture seule** afin de ne pas modifier le serveur.

## Lancer FUTMNG

Sous Windows, double-clique sur :

`OUVRIR FUTMNG.bat`

FUTMNG essaye de détecter automatiquement le dossier `serveur fifa 17`. Si le serveur n'est pas détecté, utilise le bouton **Choisir le serveur**.

## Structure du dépôt

```text
FUTMNG/
├── app/                 # application principale
├── assets/              # ressources visuelles / cache
├── config/              # configuration FUTMNG
├── updater/             # futur système de mise à jour
├── version.json         # version installée
└── OUVRIR FUTMNG.bat    # lancement Windows
```

## Projet

FUTMNG a vocation à devenir l'interface centrale de consultation des joueurs, cartes, SBC, packs, coupes et événements du serveur MNG FUT FIFA 17.


## Mise à jour automatique

FUTMNG v1.0.1 ajoute le bouton **↻ MISE À JOUR**. Il vérifie la dernière GitHub Release du dépôt `Minegamerfrance/FUTMNG`, télécharge le ZIP de la nouvelle version, sauvegarde les fichiers remplacés puis relance l'application.

Pour publier une version, attacher à la Release un ZIP nommé par exemple `FUTMNG-GitHub-v1.0.2.zip`.
