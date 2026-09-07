# FUTMNG Updater

Le module `futmng_updater.py` installe les nouvelles versions publiées dans **GitHub Releases**.

Flux :
1. FUTMNG interroge `Minegamerfrance/FUTMNG` pour la dernière Release publique.
2. Il cherche un fichier ZIP dont le nom commence par `FUTMNG`.
3. Après confirmation, le ZIP est téléchargé dans le dossier temporaire Windows.
4. FUTMNG ferme l'application et lance l'updater.
5. Les fichiers remplacés sont sauvegardés dans `backups/before-update-AAAA...`.
6. La nouvelle version est copiée puis FUTMNG redémarre.

Les dossiers `.git`, `cache`, `logs`, `user-data` et `backups` ne sont jamais remplacés par une Release.
