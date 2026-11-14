"""Encrypted, consent-based cross-call preferences; no free-form disclosures."""
import hashlib
import hmac
import json

import psycopg
from cryptography.fernet import Fernet


class SupportMemoryStore:
    def __init__(self, database_url, encryption_key, identity_key, retention_days=30):
        if not database_url or not encryption_key or not identity_key:
            raise ValueError("memory database, encryption and identity keys are required")
        if not 1 <= retention_days <= 365:
            raise ValueError("retention must be between 1 and 365 days")
        self.database_url = database_url
        self.fernet = Fernet(encryption_key.encode())
        self.identity_key = identity_key.encode()
        self.retention_days = retention_days

    def _key(self, caller_id):
        return hmac.new(self.identity_key, caller_id.encode(), hashlib.sha256).hexdigest()

    def read(self, caller_id):
        key = self._key(caller_id)
        with psycopg.connect(self.database_url) as con:
            row = con.execute("SELECT ciphertext FROM caller_support_memory WHERE caller_key=%s AND expires_at > now()", (key,)).fetchone()
            if not row:
                return None
            con.execute("INSERT INTO caller_memory_audit (caller_key,action) VALUES (%s,'read')", (key,))
            return json.loads(self.fernet.decrypt(bytes(row[0])))

    def save(self, caller_id, preferences, *, consent=False, matched=False):
        key = self._key(caller_id)
        token = self.fernet.encrypt(json.dumps(preferences).encode())
        with psycopg.connect(self.database_url) as con:
            if consent:
                if matched:
                    row = con.execute("""INSERT INTO caller_support_memory (caller_key,ciphertext,consented_at,expires_at)
                        VALUES (%s,%s,now(),now() + %s * interval '1 day')
                        ON CONFLICT (caller_key) DO UPDATE SET ciphertext=EXCLUDED.ciphertext,
                        consented_at=now(),updated_at=now(),expires_at=EXCLUDED.expires_at RETURNING caller_key""",
                        (key, token, self.retention_days)).fetchone()
                else:
                    row = con.execute("""INSERT INTO caller_support_memory (caller_key,ciphertext,consented_at,expires_at)
                        VALUES (%s,%s,now(),now() + %s * interval '1 day')
                        ON CONFLICT (caller_key) DO NOTHING RETURNING caller_key""",
                        (key, token, self.retention_days)).fetchone()
                if not row:
                    return False
                action = 'consent'
            else:
                row = con.execute("""UPDATE caller_support_memory SET ciphertext=%s, updated_at=now()
                    WHERE caller_key=%s AND expires_at > now() RETURNING caller_key""", (token,key)).fetchone()
                if not row:
                    return False
                action = 'update'
            con.execute("INSERT INTO caller_memory_audit (caller_key,action) VALUES (%s,%s)", (key,action))
        return True

    def delete(self, caller_id):
        key = self._key(caller_id)
        with psycopg.connect(self.database_url) as con:
            con.execute("DELETE FROM caller_support_memory WHERE caller_key=%s", (key,))
            con.execute("INSERT INTO caller_memory_audit (caller_key,action) VALUES (%s,'delete')", (key,))

    def purge_expired(self):
        with psycopg.connect(self.database_url) as con:
            con.execute("DELETE FROM caller_support_memory WHERE expires_at <= now()")
