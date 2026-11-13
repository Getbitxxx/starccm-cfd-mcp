import json
from backend import Backend

if __name__=='__main__':
    print(json.dumps(Backend().deploy(),indent=2))
