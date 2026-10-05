-- Consumer pipeline: add 'negotiating' and 'repeat_customer' (legacy first_call / referral are kept)
ALTER TABLE lead_stages
    MODIFY COLUMN stage ENUM('new_lead', 'first_call', 'aware', 'interested', 'quoted', 'negotiating', 'won', 'lost', 'referral', 'repeat_customer') NOT NULL;
