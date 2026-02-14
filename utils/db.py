# utils/db.py
from pymongo import MongoClient
from config import Config

class Database:
    _instance = None
    _client = None
    _db = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._client is None:
            self.connect()
    
    def connect(self):
        """เชื่อมต่อกับ MongoDB"""
        try:
            self._client = MongoClient(Config.MONGODB_URI)
            self._db = self._client[Config.DATABASE_NAME]
            # ทดสอบการเชื่อมต่อ
            self._client.admin.command('ping')
            print(f"✅ Connected to MongoDB: {Config.DATABASE_NAME}")
        except Exception as e:
            print(f"❌ MongoDB Connection Error: {e}")
            raise
    
    def get_db(self):
        """ดึง database instance"""
        if self._db is None:
            self.connect()
        return self._db
    
    def get_collection(self, collection_name):
        """ดึง collection"""
        return self.get_db()[collection_name]
    
    def close(self):
        """ปิดการเชื่อมต่อ"""
        if self._client:
            self._client.close()
            print("🔌 MongoDB connection closed")

# สร้าง singleton instance
db = Database()

def get_db():
    """ฟังก์ชันสำหรับดึง database"""
    return db.get_db()

def get_collection(collection_name):
    """ฟังก์ชันสำหรับดึง collection"""
    return db.get_collection(collection_name)