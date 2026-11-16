# Déploiement sur la VM Oracle (pas à pas)

Tout ce qui suit s'exécute **sur la VM**, par vous. Le collecteur vit dans un seul dossier et un seul
conteneur (`tc-collector`) : il n'ouvre aucun port, ne touche à aucun autre conteneur, ne modifie aucune
configuration système. Dans toutes les commandes, `dc` désigne :

```bash
alias dc='docker compose -f docker/compose.yaml'   # à retaper après chaque reconnexion, ou noter à part
```

## 0. Méthode rapide (recommandée) : le script `scripts/vm.sh`

Après avoir créé le dataset et le jeton Hugging Face (section 2) :

```bash
cd ~
git clone https://github.com/MerabtiAmar/train-et-correspondances.git train-correspondances
cd train-correspondances
bash scripts/vm.sh install     # vérifie, pose 3 questions, construit, démarre, contrôle la sécurité
```

Ensuite : `bash scripts/vm.sh status` (état, mémoire, ports, derniers évènements),
`configure` (changer jeton/dataset/contact), `update`, `stop`, `start`, `uninstall`.
Le script fait exactement les étapes des sections 1 à 9 ci-dessous, qui restent la référence si vous
préférez tout taper à la main. Il ne touche qu'à ce dossier et au conteneur `tc-collector`.

## 1. Prérequis (lecture seule, rien n'est modifié)

```bash
uname -m                      # x86_64 ou aarch64 : l'image se construit ici, les deux marchent
docker --version
docker compose version        # il faut le plugin « compose » v2
git --version
df -h ~                       # au moins 5 Go libres (le collecteur plafonne à 2 Go dans data/)
free -m                       # notez la colonne « available » : voir la section sécurité
docker ps                     # notez la liste et les durées « Up » des conteneurs existants
sudo ss -tlnp | tee ~/ports-avant.txt     # photo des ports ouverts AVANT
```

Si `docker compose version` échoue, ne rien installer au hasard : demandez-moi d'abord.

## 2. Hugging Face (dans votre navigateur, une seule fois)

1. Créez un dataset **public** (ex. `votre-pseudo/train-correspondances-gtfs-rt`). Le code ne crée jamais le dépôt.
2. Créez un jeton d'accès *fine-grained* avec le droit **d'écriture sur ce seul dataset**.
3. Gardez-le pour l'étape 4. Ne le collez jamais dans un chat, un ticket ou un commit.

## 3. Récupérer le code

```bash
cd ~
git clone https://github.com/MerabtiAmar/train-et-correspondances.git train-correspondances
cd train-correspondances
```

## 4. Configurer `.env`

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

À remplir : `CONTACT_EMAIL` (envoyé dans le User-Agent, pour que le fournisseur puisse vous joindre),
`HF_REPO` (`votre-pseudo/nom-du-dataset`) et `HF_TOKEN`. Le reste a de bonnes valeurs par défaut.
**Sans `HF_TOKEN`, le collecteur tourne en « dry-run »** : il collecte et garde tout sur disque, sans rien envoyer.

## 5. Dossier de données

```bash
mkdir -p data
sudo chown 10001:10001 data     # le conteneur tourne sous l'utilisateur 10001 (non-root)
```

Cette commande ne concerne que le dossier `data/` du projet.

## 6. Construire et démarrer

```bash
dc up -d --build
```

La construction dure quelques minutes (plus sur ARM). Elle consomme de la mémoire pendant ce temps :
lancez-la de préférence hors d'un appel vidéo, après avoir regardé `free -m` (voir section sécurité).

## 7. Vérifier que ça tourne

```bash
dc logs --tail 30                                     # JSON, une ligne par évènement
docker inspect -f '{{.State.Health.Status}}' tc-collector    # « starting » puis « healthy » (≈ 2 min)
dc exec collector python -m tc.health                 # « ok, heartbeat … s old », code retour 0
ls -lh data/reduced/*/ data/coverage/*/               # fichiers horaires et rapport de couverture
```

À chercher dans les logs :
- `collector started` avec `"dry_run": false` si le jeton est bien lu (`true` = pas d'envoi).
- `trip updates source` : l'URL utilisée (la principale, sinon la secondaire).
- `no usable trip updates feed this cycle` : flux vide ou périmé. **C'est un état normal** (arrivé des jours
  entiers en 2025) : le collecteur continue et le compte dans le rapport.
- `day uploaded` : le lendemain, après 03:00 heure de Paris, la veille part sur Hugging Face puis le
  dossier local est supprimé. Vérifiez le dataset dans votre navigateur ce premier lendemain.
- Une ligne `ERROR` : copiez-la moi.

## 8. Sécurité de l'application existante

Le collecteur est limité par le noyau (cgroups) : il ne peut pas dépasser ces plafonds, même en cas de bogue.

**Confirmer que les plafonds sont appliqués :**

```bash
docker stats --no-stream tc-collector
# colonne MEM USAGE / LIMIT : « … / 768MiB » ; CPU % ne dépasse pas ≈ 50 %
docker inspect -f 'cpu={{.HostConfig.NanoCpus}} mem={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} pids={{.HostConfig.PidsLimit}}' tc-collector
# attendu : cpu=500000000 mem=805306368 swap=805306368 pids=128
```

**Confirmer qu'aucun port n'est ouvert** (comparaison avec la photo de l'étape 1) :

```bash
sudo ss -tlnp > ~/ports-apres.txt
diff ~/ports-avant.txt ~/ports-apres.txt && echo "IDENTIQUE : aucun port ajouté"
docker port tc-collector                   # ne doit rien afficher
docker inspect -f '{{json .NetworkSettings.Ports}}' tc-collector    # attendu : {}
```

**Confirmer que l'application existante n'a pas bougé :** `docker ps` doit montrer les mêmes conteneurs
qu'à l'étape 1, avec des durées « Up » qui continuent de croître (aucun redémarrage).

**Si la mémoire est juste :**
1. Regardez `free -m` (colonne « available ») et `docker stats --no-stream`. Relevez l'usage normal du
   collecteur après une heure de marche et notez-le, c'est la valeur de référence. Ordre de grandeur
   mesuré en local : 150 à 250 Mo en marche normale (conversion quotidienne du GTFS statique
   comprise), pic possible vers 350 Mo vers 03:00 pendant la compaction de la veille (ADR 0005).
   Prévoyez donc environ 400 Mo de libres dans « available » avant de démarrer.
2. Si le collecteur dépasse 768 Mo, le noyau tue **lui seul** (`docker inspect -f '{{.State.OOMKilled}}' tc-collector`
   affiche `true`) et Docker le redémarre : l'autre application n'est pas touchée. Prévenez-moi avec les logs.
3. Si l'appel vidéo en souffre quand même, arrêtez le collecteur en une commande (section 9) et ne
   touchez à rien d'autre. L'arrêt est propre (l'heure en cours est écrite) ; seules les minutes
   pendant lesquelles il est arrêté ne sont pas collectées, le flux temps réel ne se rattrape pas.
4. Ne baissez `mem_limit` (dans `docker/compose.yaml`) qu'après avoir mesuré l'usage réel, et jamais
   sous 512m sans me demander.
5. Ne modifiez ni le swap ni la configuration système de la VM pour ça.

## 9. Arrêter, désinstaller, mettre à jour

**Arrêter en une commande** (SIGTERM : l'heure en cours est écrite sur disque avant la sortie) :

```bash
dc down
```

Les données restent dans `data/`. Pour redémarrer : `dc up -d`.

**Désinstaller complètement** — d'abord vérifiez qu'il ne reste rien à envoyer
(`ls data/reduced data/alerts data/raw`) : ce qui n'a pas été envoyé sur Hugging Face est perdu.

```bash
dc down --rmi local          # arrête et supprime le conteneur et l'image tc-collector:local
cd ~ && rm -r train-correspondances
```

**Mettre à jour :**

```bash
cd ~/train-correspondances
git pull
dc up -d --build             # reconstruit et redémarre ; .env et data/ sont conservés
dc logs --tail 20
```

## 10. Dépannage rapide

| Symptôme | Que faire |
|---|---|
| `Permission denied` sur `/app/data` dans les logs | `sudo chown 10001:10001 data` puis `dc restart` |
| `dry_run: true` alors que vous avez mis un jeton | `HF_REPO` ou `HF_TOKEN` vide dans `.env` ; corrigez puis `dc up -d` |
| `upload failed` | dépôt inexistant, jeton sans droit d'écriture, ou réseau : les fichiers sont gardés, nouvel essai toutes les heures |
| `disk cap exceeded` | rien n'est supprimé tant que ce n'est pas envoyé ; réglez d'abord l'envoi. Les échantillons bruts sont mis en pause en attendant |
| conteneur `unhealthy` | `dc logs --tail 100` ; la boucle est bloquée ou arrêtée, copiez-moi les lignes `ERROR` |
