import boto3
import json
import base64
from PIL import Image
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
import os
from datetime import datetime
import concurrent.futures

from botocore.config import Config

config = Config(
   retries = {
      'max_attempts': 10,
      'mode': 'adaptive'
   }
)
# Create a Boto3 STS client
sts_client = boto3.client('sts')

#Starts the Session with the assumed role
session = boto3.Session()


#Get SSM Parameter values for OpenSearch Domain and
ssm = session.client('ssm')
parameters = ['/car-repair/collection-domain-name', '/car-repair/s3-bucket', '/car-repair/s3-bucket-source'] 
response = ssm.get_parameters(
    Names=parameters,
    WithDecryption=True
)
#Set OpenSearch Details
parameter1_value = response['Parameters'][0]['Value']
coll_domain_name = parameter1_value[8:]
os_host = coll_domain_name #collection host name from the cloudformation template. DO NOT ADD https://
os_index_name = 'repair-cost-data' #os index name that will be created

#Set S3 Details where images will be stored
parameter2_value = response['Parameters'][1]['Value']
s3_bucket = parameter2_value #bucket name from the cloudformation template
s3_folder = 'repair-data/'
parameter3_value = response['Parameters'][2]['Value']
s3_bucket_source = parameter3_value

#Initialize OpenSearch Client
credentials = session.get_credentials()
client = session.client('opensearchserverless')
service = 'aoss'
region = session.region_name
awsauth = AWS4Auth(credentials.access_key, credentials.secret_key,
                   region, service, session_token=credentials.token)

#Set MultiModal Embeddings, bedrock client and S3 client
model_name = "amazon.titan-embed-image-v1"
bedrock = session.client("bedrock-runtime", config=config)
s3 = session.client('s3')

#Define the JSON Creation metadata instruction for each car Make/Model
instruction_model_1 = 'Instruction: You are a damage repair cost estimator and based on the image you need to create a json output as close as possible to the <model>, \
you need to estimate the repair cost to populate within the output and you need to provide the damage_severity according to the <criteria>, \
you also need to provide a damage_description which is short and less than 10 words. Just provide the json output in the response, do not explain the reasoning. \
For testing purposes assume the image is from a fictitious car brand "Make_1" and a ficticious model "Model_1" in the state of Florida.'

instruction_model_2 = 'Instruction: You are a damage repair cost estimator and based on the image you need to create a json output as close as possible to the <model>, \
you need to estimate the repair cost to populate within the output and you need to provide the damage_severity according to the <criteria>, \
you also need to provide a damage_description which is short and less than 10 words. Just provide the json output in the response, do not explain the reasoning. \
For testing purposes assume the image is from a fictitious car brand "Make_2" and a ficticious model "Model_2" in the state of Florida.'

instruction_model_3 = 'Instruction: You are a damage repair cost estimator and based on the image you need to create a json output as close as possible to the <model>, \
you need to estimate the repair cost to populate within the output and you need to provide the damage_severity according to the <criteria>, \
you also need to provide a damage_description which is short and less than 10 words. Just provide the json output in the response, do not explain the reasoning. \
For testing purposes assume the image is from a fictitious car brand "Make_3" and a ficticious model "Model_3" in the state of Florida.'

bedrock_client = session.client('bedrock-runtime', config=config)


#Define function that will Create the JSON Metadata for the given damage image
def create_json_metadata(encoded_image, instruction, file_key):
    print('Sending JSON Creation Request to Bedrock Claude')
    
    # Detect image format from file extension
    file_extension = file_key.lower().split('.')[-1]
    if file_extension in ['jpg', 'jpeg']:
        media_type = "image/jpeg"
    elif file_extension == 'png':
        media_type = "image/png"
    else:
        # Default to jpeg for unknown formats
        media_type = "image/jpeg"
    
    model = '<model>\
    { \
        "make": "XXXX",\
        "model": "XXXX", \
        "year": XXXX, \
        "state": "XX",\
        "damage": "Right Front",\
        "repair_cost": XXXXX,\
        "damage_severity": "moderate",\
        "damage_description": "Dent and scratches on the right fender",\
        "parts_for_repair": "add a list of parts that need to be replaced/repaired",\
        "labor_hours": XX,\
        "parts_cost": XXXX,\
        "labor_cost" \
      }\
    </model>'
    criteria_cost = '<criteria> \
    repair_cost < 500 = light \
    repair_cost > 500 AND < 1000 = moderate \
    repair_cost > 1000 AND < 2000 = severe \
    repair_cost > 2000 = major \
    </criteria>'
    prompt = criteria_cost + model + instruction 
    invoke_body = {
    'anthropic_version': 'bedrock-2023-05-31',
    'max_tokens': 2000,
    'temperature': 1,
    'top_p': 1,
    'top_k': 250,
    'messages': [
        {
        'role': 'user',
        'content': [
            {
            "type": "image",
            "source": {
              "type": "base64",
              "media_type": media_type,
              "data": encoded_image
            }
          },
          {
            "type": "text",
            "text": prompt
          }
            ]
        }   
    ]
    }
    invoke_body = json.dumps(invoke_body).encode('utf-8')
    response = bedrock_client.invoke_model(
        body=invoke_body,
        contentType='application/json',
        accept='application/json',
        modelId='anthropic.claude-3-haiku-20240307-v1:0'
    )
    response_body = response['body'].read()
    data = json.loads(response_body)

    text = data['content'][0]['text']
    print('JSON output Created by Claude', text)
    return text 

#Function to get OpenSearch client
def get_OpenSearch_client(os_host, os_index_name):
    print("opening opensearch client")
    try:
        client = OpenSearch(
            hosts=[{'host': os_host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=300
        )

        print('Testing if an index already exists if not, create one.')

        exists = client.indices.exists(index=os_index_name)
        if not exists:
            # Create index
            response = client.indices.create(
                index=os_index_name,
                body={
                    "settings": {
                        "index": {
                        "knn": 'true',
                        "knn.algo_param.ef_search": 512
                        }
                    },
                    "mappings": {
                        "properties": {
                        "damage_vector": {
                            "type": "knn_vector",
                            "dimension": 1024,
                            "method": {
                            "name": "hnsw",
                            "space_type": "l2",
                            "engine": "nmslib",
                            "parameters": {
                                "ef_construction": 128,
                                "m": 24
                            }
                            }
                        }
                        }
                    }
                    }
            )
            print("Index created!")

        else:
            print("Index already exists, Proceeding with adding the Document")

    except Exception as e:
        print('Error opening Opensearch client: ', os_host)
        print(f"Error message: {e}")
        #os.exit(1)

    return client


#Define Function that will add the vector document along with its metadata to OpenSearch
def indexData(client, image_vector, metadata, os_index_name, os_host):
    # Build the OpenSearch client

    print('Storing Document on OpenSearch')

    # Add a document to the index.
    response = client.index(
        index=os_index_name,
        body={
            'damage_vector': image_vector,
            'metadata': metadata
        }
    )
    print('\nDocument added:')
    print(response)


def ingest_image_s3(file_contents, start_index, end_index, client, file_key , os_index_name, os_host, instruction):
    print('-----------------------------')
    print('Document Ingestion Process Starting')
    print('-----------------------------')
    batch_files = list(file_contents.items())[start_index:end_index]
    for file_key, file_binary in batch_files:

        encoded_image = base64.b64encode(file_binary).decode('utf-8')
        json_text = create_json_metadata(encoded_image, instruction, file_key)
        json_string = json.dumps(json_text)
        data_2 = json.loads(json_string)
        json_bytes = data_2.encode('utf-8')
        base64_bytes = base64.b64encode(json_bytes)
        encoded_json = base64_bytes.decode('utf-8')
        data = json.loads(json_text)
        ##upload image to the front end bucket
        key = s3_folder + file_key
        s3.put_object(Body=file_binary, Bucket=s3_bucket, Key=key)
        body = json.dumps({
                "inputText": encoded_json,
                "inputImage": encoded_image,
                "embeddingConfig": {
                    "outputEmbeddingLength": 1024
                }
            })
        print('Invoking Embeddings model: ', model_name)
        response = bedrock.invoke_model(modelId=model_name, body=body)
        body = response['body']
        body_output = body.read()
        data['s3_location'] = key
        print(data)
        metadata = data
        body_string = body_output.decode('utf-8')
        data_embedded = json.loads(body_string)
        image_vector = data_embedded['embedding']
        json_embedding = json.loads(body_string)
        indexData(client, image_vector, metadata, os_index_name, os_host)

def list_and_load_s3_files(os_index_name, os_host, instruction_model_1, instruction_model_2, instruction_model_3):
    s3 = boto3.client('s3')
    file_contents = {}
    client = get_OpenSearch_client(os_host, os_index_name)
    try:
        # List all objects in the bucket
        response = s3.list_objects_v2(Bucket=s3_bucket_source)
        # If the bucket is not empty
        if 'Contents' in response:
            # Loop through each file in the bucket
            for obj in response['Contents']:
                file_key = obj['Key']
                # Get the binary content of the file
                file_obj = s3.get_object(Bucket=s3_bucket_source, Key=file_key)
                file_content = file_obj['Body'].read()
                #print(file_content)
                #ingest_image_s3(client, file_key, file_content, os_index_name, os_host, instruction)
                # Store the file content in the dictionary
                file_contents[file_key] = file_content
                        # Process the first batch of 90 files
            ingest_image_s3(file_contents, 0, 90, client, file_key, os_index_name, os_host, instruction_model_1)

            # Process the second batch of 90 files
            ingest_image_s3(file_contents, 91, 180, client, file_key, os_index_name, os_host, instruction_model_2)

            # Process the remaining files
            ingest_image_s3(file_contents, 181, len(file_contents), client, file_key, os_index_name, os_host, instruction_model_3)
    except Exception as e:
        print(f"Error: {e}")

    return file_contents

#store current date time
start_time = datetime.now()

#print the start date time
print("Start date and time: ")
print(start_time)
print('-----------------------------')
list_and_load_s3_files(os_index_name, os_host, instruction_model_1, instruction_model_2, instruction_model_3)

#print the end date time
end_time = datetime.now()
print(f"Start time {start_time} - End date and time {end_time}") 
print('-----------------------------')
