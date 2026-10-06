-- Consumer pipeline: add 'connected', 'negotiating' and 'repeat_customer' (legacy referral is kept)
ALTER TABLE lead_stages
    MODIFY COLUMN stage ENUM('new_lead', 'first_call', 'connected', 'aware', 'interested', 'quoted', 'negotiating', 'won', 'lost', 'referral', 'repeat_customer') NOT NULL;
