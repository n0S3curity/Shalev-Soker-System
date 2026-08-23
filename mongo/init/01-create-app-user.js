// Runs once, on an empty data directory.
// Creates a least-privilege application user: readWrite on the app database
// only, never root. The root account stays for administration and backups.
(function () {
  const dbName = process.env.APP_DB || "surveys";
  const user = process.env.APP_USER || "surveys_app";
  const password = process.env.APP_PASSWORD;

  if (!password) {
    print("[init] APP_PASSWORD is empty - refusing to create the application user");
    throw new Error("APP_PASSWORD not set");
  }

  const appDb = db.getSiblingDB(dbName);

  const existing = appDb.getUser(user);
  if (existing) {
    print("[init] application user already exists: " + user);
    return;
  }

  appDb.createUser({
    user: user,
    pwd: password,
    roles: [{ role: "readWrite", db: dbName }],
  });

  print("[init] created application user '" + user + "' on database '" + dbName + "'");
})();
