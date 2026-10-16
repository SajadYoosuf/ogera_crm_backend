-- Partner pipeline: add 'demo_introduction', 'active_referral' and 'inactive' (consumer stages are unchanged)
ALTER TABLE lead_stages
    MODIFY COLUMN stage ENUM('new_lead', 'first_call', 'connected', 'aware', 'interested', 'quoted', 'negotiating', 'won', 'lost', 'referral', 'repeat_customer', 'demo_introduction', 'active_referral', 'inactive') NOT NULL;
