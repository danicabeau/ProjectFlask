from huggingface_hub import login, upload_folder

# (optional) Login with your Hugging Face credentials
login()

# Push your model files
upload_folder(folder_path=r"D:\ProjectFlask\models\Typhoon-OCR-HighDetail-Model", repo_id="danicabeau/TyphoonOCR", repo_type="model")
