// DEV ONLY seed data and credentials.
const shop = db.getSiblingDB("shop");
shop.customers.insertMany([
  { name: "Ada Lovelace", email: "ada@example.test" },
  { name: "Alan Turing", email: "alan@example.test" },
  { name: "Grace Hopper", email: "grace@example.test" },
]);
shop.orders.insertMany([
  { customer: "Ada Lovelace", total: 19.99 },
  { customer: "Alan Turing", total: 5.0 },
]);

// Read-only backup user (DESIGN.md 8.3).
db.getSiblingDB("admin").createUser({
  user: "backup",
  pwd: "dev-only-backup-pw",
  roles: [{ role: "backup", db: "admin" }],
});
