import streamlit as st
import time
import uuid
import os
from datetime import datetime

from main import process_impact_and_recommendation, process_contemplation_impact_and_recommendation_parallel

model_options = ["gemini-exp-1206", "gemini-1.5-pro", "gemini-2.0-flash-exp"]
approach_options = ["All at once", "One at a time - with Contemplator"]

def save_uploaded_file(uploaded_file, folder = "uploaded_pdfs"):
    """
    Save uploaded file with a unique ID and return the filename
    """
    # Create folder if it doesn't exist
    if not os.path.exists(folder):
        os.makedirs(folder)
    
    # Generate unique filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    filename = f"{timestamp}_{unique_id}_{uploaded_file.name}"
    file_path = os.path.join(folder, filename)
    
    # Save the file
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getvalue())
    
    return file_path

def process_pdfs_with_llm(trigger_pdf_path, company_pdf_path, model, approach) -> str:
    """
    Process the PDF files with an LLM.
    Replace this with your actual LLM implementation.
    """
    try:
        if approach == approach_options[0]:
            response = process_impact_and_recommendation(trigger_pdf_path, company_pdf_path, model)
        elif approach == approach_options[1]:
            response = process_contemplation_impact_and_recommendation_parallel(trigger_pdf_path, company_pdf_path, model)
    except Exception as e:
        response = "Something went wrong.. :/\n"
        response += str(e)
    
    # Return mock response - Replace with actual LLM response
    return response

def main():
    st.title("Trigger Event - Company Constant - Suggest Services")

    # Custom CSS to hide the Streamlit toolbar
    st.markdown("""
        <style>
            .stAppToolbar {
                display: none;
            }
        </style>
    """, unsafe_allow_html=True)
    
    # File uploaders
    trigger_pdf = st.file_uploader(
        "Upload Trigger PDF",
        type = ['pdf'],
        accept_multiple_files = False,
        key = "trigger_pdf"
    )
    
    company_pdf = st.file_uploader(
        "Upload Company Book PDF",
        type = ['pdf'],
        accept_multiple_files = False,
        key = "company_pdf"
    )

    # Model Selector
    selected_model = st.selectbox("Select Model", options = model_options, index = 0)

    # Approach Selector
    selected_approach = st.selectbox("Select Approach", options = approach_options, index = 0)
    
    # GO button
    if trigger_pdf is not None and company_pdf is not None:
        if st.button("GO"):
            with st.spinner("Processing..."):
                # Save files
                trigger_pdf_path = save_uploaded_file(trigger_pdf)
                company_pdf_path = save_uploaded_file(company_pdf)
                
                st.info(f"Files saved as:\n- {os.path.basename(trigger_pdf_path)}\n- {os.path.basename(company_pdf_path)}")
                
                # Process files
                result = process_pdfs_with_llm(trigger_pdf_path, company_pdf_path, selected_model, selected_approach)

                # Check the type of result
                if isinstance(result, dict) == False:
                    st.error("Processing failed or unexpected response format.")
                    st.error(result)  # Display the string directly
                elif "error" in result:
                    st.error(result)
                else:
                    st.success("Processing complete!")
                    st.write(f"Time taken: {result["time_taken"]:.6f} seconds")

                    if type(result["response"]) == list:
                        for idx, response in enumerate(result["response"]):
                            with st.expander(f"Recommendation #{idx+1}", expanded=True):
                                st.text(response)
                    else:
                        with st.expander("Analysis - Recommendation Response", expanded=True):
                            st.text(result["response"])

                    with st.expander("Cost", expanded=False):
                        st.write(f"Total cost: ${result["total_cost"]:.6f}")

                # Delete the save file
                os.remove(trigger_pdf_path)
                os.remove(company_pdf_path)

if __name__ == "__main__":
    main()