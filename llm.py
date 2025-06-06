from openai import OpenAI
import pandas as pd
import json
import tempfile
import boto3
from c0_utils import connect_snowflake

client = OpenAI()
s3_client = boto3.client('s3')

def fetch_rmse_metrics_from_snowflake(table_name="RMSE_RESULTS"):
    """
    Fetch RMSE metrics from Snowflake and return as a structured dictionary.
    """
    conn = connect_snowflake()
    query = f"SELECT KEYPOINT_NAME, RMSE FROM {table_name};"
    
    df = pd.read_sql(query, conn)
    conn.close()

    # Convert dataframe to dictionary
    rmse_metrics = {
        "3D_RMSE": {row["KEYPOINT_NAME"]: row["RMSE"] for _, row in df.iterrows() if "ANGLE" not in row["KEYPOINT_NAME"]},
        "knee_angle_rmse": df[df["KEYPOINT_NAME"] == "KNEE_ANGLE"]["RMSE"].values[0],
        "hip_abduction_angle_rmse": df[df["KEYPOINT_NAME"] == "HIP_ABDUCTION_ANGLE"]["RMSE"].values[0]
    }

    return rmse_metrics

def generate_physio_report(patient_info, rmse_metrics, gif_s3_url, bucket_name, report_s3_path):
    """
    Generates a physiotherapy feedback report using OpenAI's GPT-4 with Chain-of-Thought prompting.
    
    Parameters:
      - patient_info (dict): Contains age, height, weight, etc.
      - rmse_metrics (dict): Contains 3D RMSE and angle RMSE values from Snowflake.
      - overlay_gif_path (str): Path to the overlay skeleton animation.
      
    Returns:
      - dict: A structured chain-of-thought output containing step-by-step reasoning and the final report.
    """
    # Construct the prompt text
    prompt = f"""
    You are an AI physiotherapy assistant. Your task is to analyze a patient's movement based on RMSE metrics,
    patient information, and an overlay skeleton animation. Use logical reasoning and a step-by-step chain-of-thought approach to provide corrective feedback and suggestions.

    **Patient Information:**
    - Age: {patient_info['age']} years
    - Height: {patient_info['height']} cm
    - Weight: {patient_info['weight']} kg

    **3D RMSE for Selected Keypoints:**
    {", ".join([f"{k}: {v:.4f}" for k, v in rmse_metrics['3D_RMSE'].items()])}

    **Angle RMSE:**
    - Knee Angle RMSE: {rmse_metrics['knee_angle_rmse']:.2f}°
    - Hip Abduction Angle RMSE: {rmse_metrics['hip_abduction_angle_rmse']:.2f}°

    **Overlay Skeleton Animation:**  
    The animation is provided as an image input below.

    **Step-by-Step Analysis:**
    1. Identify which keypoints have the highest RMSE values and explain their implications.
    2. Correlate these values with common physiotherapy movement errors.
    3. Provide explanations for these errors based on biomechanics.
    4. Suggest specific corrective actions the patient can take.
    5. Adjust your recommendations based on the patient’s demographics.

    **Feedback Report:**
    Provide a detailed, structured report using the above reasoning.
    """
    
    # Define user input
    user_input_content = [
        {
            "type": "input_text",
            "text": prompt
        },
        {
            "type": "input_image",
            "image_url": gif_s3_url
        }
    ]


    # Define the expected output schema for chain-of-thought reasoning
    schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "explanation": {"type": "string"},
                        "suggestion": {"type": "string"}
                    },
                    "required": ["explanation", "suggestion"],
                    "additionalProperties": False
                }
            },
            "final_report": {"type": "string"}
        },
        "required": ["steps", "final_report"],
        "additionalProperties": False
    }
    
    # Call OpenAI's API with the structured output format
    response = client.responses.create(
        model="gpt-4o-2024-08-06",
        input=[
            {
                "role": "system",
                "content": "You are an expert physiotherapist providing AI-driven motion correction insights."
            },
            {
                "role": "user",
                "content": user_input_content
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "physio_feedback",
                "schema": schema,
                "strict": True
            }
        },
        max_output_tokens=1000
    )
    
    # Parse the structured output
    report = json.loads(response.output_text)
    physio_feedback = json.dumps(report, indent=2)

    # Save the feedback JSON temporarily
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as temp_json:
        json.dump(physio_feedback, temp_json, indent=2)
        temp_json.flush()
        temp_path = temp_json.name

    # Upload to S3
    s3_client.upload_file(temp_path, bucket_name, report_s3_path)
    print(f"[LLM] Physiotherapy Generated report uploaded to s3://{bucket_name}/{report_s3_path}")
    
    return {
        "prompt": prompt,
        "report_dict": report,
        "report_json": physio_feedback,
        "report_s3_path": f"s3://{bucket_name}/{report_s3_path}"
    }