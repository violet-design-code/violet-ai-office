CREATE TABLE bm_profiles (business_id TEXT PRIMARY KEY REFERENCES portfolio_businesses(id), provided_services TEXT NOT NULL, audience TEXT NOT NULL, manager TEXT REFERENCES office_users(username));
CREATE TABLE bm_month_goals (business_id TEXT NOT NULL REFERENCES portfolio_businesses(id), month TEXT NOT NULL, revenue_yen INTEGER, profit_yen INTEGER, PRIMARY KEY(business_id,month));
